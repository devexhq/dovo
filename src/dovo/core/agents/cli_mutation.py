"""Shared direct-mutation agent adapter base and DTOs."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from dovo.common.process import stream_isolated_process
from dovo.common.tool_policy import ToolPolicy
from dovo.core.agents.base import BaseAgentProvider, elapsed_ms
from dovo.core.agents.environment import ENV_FILTERED_PROMPT_LINE, build_agent_env, withheld_env_names
from dovo.core.agents.models import (
    AgentDenial,
    AgentInvocationContext,
    AgentProgress,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
)
from dovo.core.agents.mutation_git import (
    MutationGitError,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)
from dovo.core.agents.responses import blocked_response, no_op_response, provider_error_response, timeout_response
from dovo.core.agents.save_rejected_patch import save_rejected_patch
from dovo.core.git import PatchApplyResult, PatchApplyStatus, validate_patch_text

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
    on_line: Callable[[str, str], None] | None = Field(default=None, exclude=True)
    on_progress: Callable[[AgentProgress], None] | None = Field(default=None, exclude=True)


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


def forward_stdout(
    request: CliMutationRunRequest, mapper: Callable[..., tuple[AgentProgress, ...]]
) -> Callable[[str], None] | None:
    """Return the stdout line callback that tags each line for on_line and maps it into on_progress, or None when neither is set.

    Mapper and on_progress failures are dropped so a bad line never kills the stream reader.
    """
    on_progress = request.on_progress
    line_callback = forward_line("stdout", request.on_line)
    if on_progress is None:
        return line_callback

    def forward(line: str) -> None:
        if line_callback is not None:
            line_callback(line)

        try:
            for progress in mapper(line, worktree_path=request.worktree_path):
                on_progress(progress)
        except Exception:
            return

    return forward


def run_cli_process(
    cmd: Sequence[str],
    request: CliMutationRunRequest,
    *,
    env: dict[str, str],
    binary: str,
    install_hint: str,
    mapper: Callable[..., tuple[AgentProgress, ...]],
) -> subprocess.CompletedProcess[bytes] | CliMutationOutcome:
    """Run cmd in the request worktree, streaming lines to the request callbacks, or return the terminal outcome for a timeout or spawn failure."""
    try:
        return stream_isolated_process(
            cmd,
            cwd=request.worktree_path,
            env=env,
            input_data=request.prompt.encode("utf-8"),
            timeout_seconds=request.timeout_seconds,
            on_stdout_line=forward_stdout(request, mapper),
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


def build_mutation_prompt(request: AgentRequest) -> str:
    """Build the agent prompt: direct mode carries the authored instruction only; remediation modes add the failure payload; a scratch path adds the scratch lines."""
    header = _DIRECT_PROMPT_HEADER if request.mode == "direct" else _REMEDIATION_PROMPT_HEADER
    if request.agent_scratch_path is not None:
        header += _SCRATCH_PROMPT_LINES + "\n"

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


def validate_request_patch(request: AgentRequest, diff: str) -> PatchApplyResult:
    """Run the shared patch gate on ``diff`` with the request's bounds; every bound the request leaves unset is not enforced."""
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
        started = time.monotonic()

        preflight_error = self._preflight(request)
        if preflight_error is not None:
            return provider_error_response(duration_ms=elapsed_ms(started), detail=preflight_error)

        try:
            baseline = resolve_pre_agent_baseline(request.worktree_path)
        except MutationGitError as exc:
            return provider_error_response(
                duration_ms=elapsed_ms(started), detail=f"Failed to resolve worktree baseline: {exc}"
            )

        env, withheld = self._build_run_env(request)
        prompt = build_mutation_prompt(request)
        if withheld:
            prompt = f"{ENV_FILTERED_PROMPT_LINE}\n{prompt}"

        outcome = self._default_run(
            CliMutationRunRequest(
                worktree_path=request.worktree_path,
                prompt=prompt,
                model=request.model,
                timeout_seconds=float(request.timeout_seconds),
                tools=request.tools,
                invocation=request.invocation,
                env=env,
                on_line=request.on_line,
                on_progress=request.on_progress,
            )
        )
        duration_ms = elapsed_ms(started)
        response = self._respond_to_outcome(request, outcome, baseline, duration_ms)

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
        duration_ms: int,
    ) -> AgentResponse:
        """Classify a finished runner outcome into the timeout, provider-error, blocked, no-op, or proposal response; denials block only when the worktree diff is empty."""
        if outcome.status == "timeout":
            return timeout_response(
                provider=self._provider_name(),
                timeout_seconds=request.timeout_seconds,
                duration_ms=duration_ms,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if outcome.status == "error":
            return provider_error_response(
                duration_ms=duration_ms,
                detail=outcome.error_detail or "Direct-mutation runner returned error",
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        try:
            diff, _ = capture_diff_since(request.worktree_path, baseline)
        except MutationGitError as exc:
            return provider_error_response(
                duration_ms=duration_ms,
                detail=f"Failed to capture worktree diff: {exc}",
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if not diff.strip() and outcome.denials:
            return blocked_response(
                duration_ms=duration_ms,
                denials=outcome.denials,
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        if not diff.strip():
            return no_op_response(duration_ms=duration_ms, raw_text=outcome.result_text, mutation_baseline_ref=baseline)

        gate = validate_request_patch(request, diff)
        if gate.status != PatchApplyStatus.CHECKED_OK:
            return self._reject_gated_edit(request, gate, outcome, baseline, duration_ms)

        return AgentResponse(
            status=AgentResponseStatus.PROPOSED_PATCH,
            unified_diff=diff,
            duration_ms=duration_ms,
            mutation_baseline_ref=baseline,
            raw_text=outcome.result_text,
        )

    def _reject_gated_edit(
        self,
        request: AgentRequest,
        gate: PatchApplyResult,
        outcome: CliMutationOutcome,
        baseline: str,
        duration_ms: int,
    ) -> AgentResponse:
        """Save the rejected edit, then discard it and return PROVIDER_ERROR carrying the gate's errors, the saved path, and any discard failure; a failed save leaves the worktree untouched."""
        saved, reason = _save_rejected_edit(request, baseline)
        if reason is not None:
            return provider_error_response(
                duration_ms=duration_ms,
                errors=[*gate.errors, f"Could not save the rejected edit ({reason}); the worktree was left unchanged."],
                raw_text=outcome.result_text,
                mutation_baseline_ref=baseline,
            )

        discard_detail: str | None = None
        try:
            discard_since(request.worktree_path, baseline)
        except MutationGitError as exc:
            discard_detail = f"Failed to discard rejected worktree edit: {exc}"

        errors = list(gate.errors)
        if saved is not None:
            errors.append(f"Rejected edit saved to {saved}." if discard_detail else f"Discarded edit saved to {saved}.")

        response = provider_error_response(
            duration_ms=duration_ms,
            detail=discard_detail,
            errors=errors,
            raw_text=outcome.result_text,
            mutation_baseline_ref=baseline,
        )

        return response.model_copy(update={"rejected_patch_path": str(saved) if saved is not None else None})


def _save_rejected_edit(request: AgentRequest, baseline: str) -> tuple[Path | None, str | None]:
    """Return ``(saved path or None, failure reason or None)``; a missing destination is a failure."""
    if request.rejected_patch_destination is None:
        return None, "no session storage directory is available"

    try:
        return save_rejected_patch(request.worktree_path, baseline, request.rejected_patch_destination), None
    except MutationGitError as exc:
        return None, str(exc)
