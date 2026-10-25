"""Shared direct-mutation agent adapter base and DTOs."""

from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import append_diagnostic_hint
from dovo.common.exceptions import Failure, catch_failure
from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.process import stream_isolated_process
from dovo.common.timing import Stopwatch
from dovo.common.tool_policy import ToolPolicy
from dovo.core.agents.base import BaseAgentProvider, ProgressMapper, ProviderSpec
from dovo.core.agents.environment import build_agent_env, env_filtered_prompt_line, withheld_env_names
from dovo.core.agents.exceptions import AgentError
from dovo.core.agents.models import (
    AgentDenial,
    AgentInvocationContext,
    AgentProgress,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
)
from dovo.core.agents.mutation_git import (
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)
from dovo.core.agents.responses import (
    blocked_response,
    no_op_response,
    provider_error_response,
    timeout_response,
    unfixable_response,
)
from dovo.core.agents.save_rejected_patch import save_rejected_patch
from dovo.core.git import validate_patch_text

CliMutationRunStatus = Literal["finished", "timeout", "error"]


class CliMutationRunRequest(BaseModel):
    """Normalized inputs for invoking a direct-mutation CLI/SDK runner."""

    model_config = {"extra": "forbid", "strict": True}

    worktree_path: Path
    prompt: str
    model: str | None = None
    timeout_seconds: float
    tools: ToolPolicy
    invocation: AgentInvocationContext | None = Field(default=None, exclude=True)
    env: dict[str, str] = Field(default_factory=dict, exclude=True)
    mcp_servers: dict[str, McpServerDefinition] = Field(default_factory=dict, exclude=True)
    on_line: Callable[[str, str], None] | None = Field(default=None, exclude=True)
    on_progress: Callable[[AgentProgress], None] | None = Field(default=None, exclude=True)
    progress_mapper: ProgressMapper | None = Field(default=None, exclude=True)


class CliMutationOutcome(BaseModel):
    """Normalized result from a direct-mutation CLI/SDK runner."""

    model_config = {"extra": "forbid", "strict": True}

    status: CliMutationRunStatus
    result_text: str | None = None
    error_detail: str | None = None
    denials: list[AgentDenial] = Field(default_factory=list)


CliMutationRunFn = Callable[[CliMutationRunRequest], CliMutationOutcome]


def forward_line(stream_name: str, on_line: Callable[[str, str], None] | None) -> Callable[[str], None] | None:
    """Return a callback that passes each line to on_line tagged with stream_name, or None when on_line is None."""
    if on_line is None:
        return None

    def forward(line: str) -> None:
        on_line(stream_name, line)

    return forward


def forward_stdout(request: CliMutationRunRequest) -> Callable[[str], None] | None:
    """Return the stdout line callback that tags each line for on_line and maps it through request.progress_mapper into on_progress, or None when neither callback is set.

    A request with no progress_mapper maps nothing. Mapper and on_progress failures are dropped so a bad line never kills the stream reader.
    """
    on_progress = request.on_progress
    mapper = request.progress_mapper
    line_callback = forward_line("stdout", request.on_line)
    if on_progress is None or mapper is None:
        return line_callback

    def forward(line: str) -> None:
        if line_callback is not None:
            line_callback(line)

        try:
            for progress in mapper(line, worktree_path=request.worktree_path):
                on_progress(progress)
        except Exception:
            # No record: progress mapping runs per line and only feeds display.
            return

    return forward


def run_cli_process(
    cmd: Sequence[str],
    request: CliMutationRunRequest,
    *,
    env: dict[str, str],
    binary: str,
    install_hint: str,
) -> subprocess.CompletedProcess[bytes] | CliMutationOutcome:
    """Run cmd in the request worktree, streaming lines to the request callbacks, or return the terminal outcome for a timeout or spawn failure."""
    try:
        return stream_isolated_process(
            cmd,
            cwd=request.worktree_path,
            env=env,
            input_data=request.prompt.encode("utf-8"),
            timeout_seconds=request.timeout_seconds,
            on_stdout_line=forward_stdout(request),
            on_stderr_line=forward_line("stderr", request.on_line),
        )
    except subprocess.TimeoutExpired:
        return CliMutationOutcome(status="timeout")
    except FileNotFoundError as error:
        return CliMutationOutcome(
            status="error",
            error_detail=f"{binary} is not installed or not on PATH: {error}. Fix: {install_hint}",
        )
    except OSError as error:
        return CliMutationOutcome(status="error", error_detail=str(error))


def decode_output(completed: subprocess.CompletedProcess[bytes]) -> tuple[str, str]:
    """Return the completed process's stdout and stderr as text, replacing undecodable bytes."""
    return (
        (completed.stdout or b"").decode("utf-8", errors="replace"),
        (completed.stderr or b"").decode("utf-8", errors="replace"),
    )


def unknown_option_detail(stderr_text: str, detail: str) -> str | None:
    """Return detail when stderr reports an unknown option, else None."""
    if "unknown option" in stderr_text.lower():
        return detail

    return None


_DIRECT_PROMPT_HEADER = (
    "You are a coding agent running directly in this worktree checkout.\n"
    "- Carry out the instruction below.\n"
    "- If it asks for planning or review, report your findings in your final message and leave the working tree unchanged.\n"
    "- Stay inside this working directory; do not push, open a PR, or touch remotes.\n"
    "- Do not modify files under .dovo/.\n\n"
)
_REMEDIATION_PROMPT_HEADER = (
    "You are a coding agent running directly in this worktree checkout. "
    "Fix the failure described below.\n"
    "- Make the smallest change that fixes the failure.\n"
    "- Stay inside this working directory; do not push, open a PR, or "
    "touch remotes.\n"
    "- Prefer leaving tests green.\n"
    "- Do not modify files under .dovo/.\n"
    "- When finished, leave the working tree containing only the fix.\n\n"
)
_SCRATCH_PROMPT_LINES = (
    "- Put temporary files only in the directory named by agent_scratch_path; make source edits in this checkout.\n"
    "- Do not read or write logs, artifacts, or session directories.\n"
)
UNFIXABLE_FILE_NAME = "unfixable.txt"
UNFIXABLE_REASON_MAX_CHARS = 1000


def _unfixable_prompt_line(scratch_path: Path) -> str:
    """Return the prompt line naming the literal unfixable-report path inside scratch_path."""
    return (
        "- If you cannot complete the instruction, write the reason, in 100 words or less, "
        f"to {scratch_path / UNFIXABLE_FILE_NAME} and stop.\n"
    )


def build_mutation_prompt(request: AgentRequest) -> str:
    """Build the agent prompt: direct mode carries the authored instruction only; remediation modes add the failure payload; a scratch path adds the scratch lines and the unfixable-report line."""
    header = _DIRECT_PROMPT_HEADER if request.mode == "direct" else _REMEDIATION_PROMPT_HEADER
    if request.agent_scratch_path is not None:
        header += _SCRATCH_PROMPT_LINES + _unfixable_prompt_line(request.agent_scratch_path) + "\n"

    return header + json.dumps(_prompt_body(request), indent=2, ensure_ascii=False)


def _prompt_body(request: AgentRequest) -> dict[str, object]:
    """Return the JSON body: mode, worktree_path, instruction, plus agent_scratch_path and payload only when the request carries them."""
    body: dict[str, object] = {"mode": request.mode, "worktree_path": str(request.worktree_path)}
    if request.agent_scratch_path is not None:
        body["agent_scratch_path"] = str(request.agent_scratch_path)

    body["instruction"] = request.instruction
    if request.payload is not None:
        body["payload"] = request.payload.model_dump(mode="json")
    return body


def validate_request_patch(request: AgentRequest, diff: str) -> list[str]:
    """Run the shared patch gate on ``diff`` with the request's bounds and return the touched files; every bound the request leaves unset is not enforced.

    Raises:
        GitError: When the diff fails a bound, is malformed, or escapes the worktree.
    """
    return validate_patch_text(
        diff,
        max_files=request.max_files,
        max_patch_kb=request.max_patch_kb,
        reject_binary_changes=request.reject_binary_changes,
        worktree_path=request.worktree_path,
    )


class CliDirectMutationAdapter(BaseAgentProvider):
    """Base for providers that edit the worktree directly instead of returning a diff.

    Edits that fail patch validation are discarded from the worktree; a failed discard is reported in the response errors.
    An agent that writes a non-blank `unfixable.txt` to its scratch directory gets UNFIXABLE, and its edits are saved and
    discarded like a rejected edit.
    """

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute the provider tool against the mutation request."""
        raise NotImplementedError

    def _preflight(self, request: AgentRequest) -> str | None:
        """Perform provider-specific preflight checks before running; they run after the base credential check."""
        return None

    def _provider_name(self) -> str:
        """Return the display name of the mutation adapter provider."""
        return "direct-mutation"

    def _invoke(self, request: AgentRequest) -> AgentResponse:
        """Run the provider in the worktree; never raises for classified outcomes."""
        watch = Stopwatch.start()

        preflight_error = self._preflight(request)
        if preflight_error is not None:
            watch.stop()
            return provider_error_response(duration_seconds=watch.elapsed_seconds, detail=preflight_error)

        try:
            baseline = resolve_pre_agent_baseline(request.worktree_path)
        except AgentError as exc:
            watch.stop()
            return provider_error_response(
                duration_seconds=watch.elapsed_seconds, detail=f"Failed to resolve worktree baseline: {exc}"
            )

        spec = self._provider_spec()
        env, withheld = self._build_run_env(request)
        prompt = build_mutation_prompt(request)
        if withheld and spec is not None:
            prompt = f"{env_filtered_prompt_line(spec.token)}\n{prompt}"
            _notify_env_withheld(request.on_env_withheld, withheld)

        outcome = self._default_run(
            CliMutationRunRequest(
                worktree_path=request.worktree_path,
                prompt=prompt,
                model=request.model,
                timeout_seconds=float(request.timeout_seconds),
                tools=request.tools,
                invocation=request.invocation,
                env=env,
                mcp_servers=request.mcp_servers,
                on_line=request.on_line,
                on_progress=request.on_progress,
                progress_mapper=_progress_mapper_for(spec),
            )
        )
        watch.stop()
        response = self._respond_to_outcome(request, outcome, baseline, watch.elapsed_seconds)

        return response.model_copy(update={"env_withheld": withheld})

    def _build_run_env(self, request: AgentRequest) -> tuple[dict[str, str], list[str]]:
        """Return the subprocess environment and the withheld host names; a provider without a descriptor gets neither."""
        spec = self._provider_spec()
        if spec is None:
            return {}, []

        env = build_agent_env(spec, request)

        return env, withheld_env_names(request, env)

    def _respond_to_outcome(
        self,
        request: AgentRequest,
        outcome: CliMutationOutcome,
        baseline: str,
        duration_seconds: float,
    ) -> AgentResponse:
        """Classify a finished runner outcome into the timeout, provider-error, unfixable, blocked, no-op, or proposal response; denials block only when the worktree diff is empty."""
        if outcome.status == "timeout":
            return timeout_response(
                provider=self._provider_name(),
                timeout_seconds=request.timeout_seconds,
                duration_seconds=duration_seconds,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if outcome.status == "error":
            return provider_error_response(
                duration_seconds=duration_seconds,
                detail=outcome.error_detail or "Direct-mutation runner returned error",
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        try:
            diff, _ = capture_diff_since(request.worktree_path, baseline)
        except AgentError as exc:
            return provider_error_response(
                duration_seconds=duration_seconds,
                detail=f"Failed to capture worktree diff: {exc}",
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        unfixable = self._unfixable_outcome(request, diff, outcome, baseline, duration_seconds)
        if unfixable is not None:
            return unfixable

        if not diff.strip() and outcome.denials:
            return blocked_response(
                provider=self._provider_name(),
                duration_seconds=duration_seconds,
                denials=outcome.denials,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if not diff.strip():
            return no_op_response(
                duration_seconds=duration_seconds, raw_text=outcome.result_text, mutation_baseline_ref=baseline
            )

        with catch_failure("agent.validate_patch", path=str(request.worktree_path)) as caught:
            validate_request_patch(request, diff)
        if caught.failure is not None:
            return self._reject_gated_edit(request, caught.failure, outcome, baseline, duration_seconds)

        return AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH,
            unified_diff=diff,
            duration_seconds=duration_seconds,
            mutation_baseline_ref=baseline,
            raw_text=outcome.result_text,
        )

    def _reject_gated_edit(
        self,
        request: AgentRequest,
        failure: Failure,
        outcome: CliMutationOutcome,
        baseline: str,
        duration_seconds: float,
    ) -> AgentResponse:
        """Save the rejected edit, then discard it and return PROVIDER_ERROR carrying the validation failure's message and any Details logged line, the saved path, and any discard failure; a failed save leaves the worktree untouched."""
        saved, notes, discard_detail = _save_and_discard(request, baseline)
        response = provider_error_response(
            duration_seconds=duration_seconds,
            detail=discard_detail,
            errors=[append_diagnostic_hint(failure.message, failure.diagnostic), *notes],
            raw_text=outcome.result_text,
            mutation_baseline_ref=baseline,
        )

        return response.model_copy(update={"rejected_patch_path": str(saved) if saved is not None else None})

    def _unfixable_outcome(
        self,
        request: AgentRequest,
        diff: str,
        outcome: CliMutationOutcome,
        baseline: str,
        duration_seconds: float,
    ) -> AgentResponse | None:
        """Return None without a non-blank unfixable report, PROVIDER_ERROR when it cannot be read, else UNFIXABLE after saving and discarding any edit in diff."""
        reason, read_error = _read_unfixable_reason(request.agent_scratch_path)
        if read_error is not None:
            return provider_error_response(
                duration_seconds=duration_seconds,
                detail=read_error,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if reason is None:
            return None

        if not diff.strip():
            return unfixable_response(
                reason=reason,
                duration_seconds=duration_seconds,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        saved, notes, discard_detail = _save_and_discard(request, baseline)

        response = unfixable_response(
            reason=reason,
            duration_seconds=duration_seconds,
            errors=[*([discard_detail] if discard_detail is not None else []), *notes],
            raw_text=outcome.result_text,
            mutation_baseline_ref=baseline,
        )

        return response.model_copy(update={"rejected_patch_path": str(saved) if saved is not None else None})


def _notify_env_withheld(callback: Callable[[list[str]], None] | None, names: list[str]) -> None:
    """Report the withheld host names to the callback before the provider runs, dropping any callback failure."""
    if callback is None:
        return

    try:
        callback(names)
    except Exception:
        # No record: the callback only feeds the operator notice and the session-log timeline; the withheld names still reach the response.
        return


def _progress_mapper_for(spec: ProviderSpec | None) -> ProgressMapper | None:
    """Return the descriptor's progress mapper, or None when the provider declares no descriptor."""
    return None if spec is None else spec.progress_mapper


def _save_rejected_edit(request: AgentRequest, baseline: str) -> tuple[Path | None, str | None]:
    """Return ``(saved path or None, failure reason or None)``; a missing destination is a failure."""
    if request.rejected_patch_destination is None:
        return None, "no session storage directory is available"

    try:
        return save_rejected_patch(request.worktree_path, baseline, request.rejected_patch_destination), None
    except AgentError as exc:
        return None, str(exc)


def _read_unfixable_reason(scratch_path: Path | None) -> tuple[str | None, str | None]:
    """Return ``(stripped reason, None)`` for a non-blank report, ``(None, None)`` without a scratch path or when the report is missing or blank, and ``(None, detail)`` when it is a symlink, not a regular file, or cannot be read.

    The report is opened once without following symlinks or blocking, checked through that descriptor, and read only
    up to its first ``UNFIXABLE_REASON_MAX_CHARS`` characters, so a swapped-in FIFO or an oversized file cannot hang or
    exhaust the process.
    """
    if scratch_path is None:
        return None, None

    path = scratch_path / UNFIXABLE_FILE_NAME
    try:
        text = _read_report_prefix(path)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        detail = "it is a symlink" if exc.errno == errno.ELOOP else exc.strerror or str(exc)
        return None, f"Failed to read {path}: {detail}"

    if text is None:
        return None, f"Failed to read {path}: it is not a regular file"

    reason = text.strip()

    return (reason or None), None


def _read_report_prefix(path: Path) -> str | None:
    """Return the first ``UNFIXABLE_REASON_MAX_CHARS`` characters of the regular file at path, or None when it is not one."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        is_regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        os.close(fd)
        raise

    if not is_regular:
        os.close(fd)
        return None

    with os.fdopen(fd, encoding="utf-8", errors="replace") as report:
        return report.read(UNFIXABLE_REASON_MAX_CHARS)


def _save_and_discard(request: AgentRequest, baseline: str) -> tuple[Path | None, list[str], str | None]:
    """Save the edit since baseline, then discard it; return ``(saved path or None, notes, discard failure detail or None)``.

    A failed save leaves the worktree untouched and returns only the could-not-save note.
    """
    saved, reason = _save_rejected_edit(request, baseline)
    if reason is not None:
        return None, [f"Could not save the rejected edit ({reason}); the worktree was left unchanged."], None

    discard_detail: str | None = None
    try:
        discard_since(request.worktree_path, baseline)
    except AgentError as exc:
        discard_detail = f"Failed to discard rejected worktree edit: {exc}"

    if saved is None:
        return None, [], discard_detail

    note = f"Rejected edit saved to {saved}." if discard_detail else f"Discarded edit saved to {saved}."

    return saved, [note], discard_detail
