"""Agent step execution: resolved provider invocation, sandbox application, and the stdout summary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from worktree.core.agents import (
    PROVIDERS,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    ProviderKind,
    ResolvedAgentSettings,
    validate_request_patch,
)
from worktree.core.agents.factory import get_agent_adapter
from worktree.core.git import GitError, GitRunner
from worktree.core.patch import GitDiffParser, PatchApplyStatus
from worktree.core.step.models import AgentStepSummary, StepDefinition, StepDispatchOutcome

SANDBOX_REQUIRED_MESSAGE = "Agent steps require an active Worktree Git sandbox.\nFix: remove --no-sandbox and run the blueprint with a sandbox."
MISSING_SETTINGS_MESSAGE = (
    "Agent step has no resolved agent settings "
    "(agent.provider, agent.model, agent.endpoint, agent.temperature, agent.max_tokens)."
)
BLANK_PROMPT_MESSAGE = "Agent step prompt is blank after interpolation."


@dataclass(frozen=True)
class _Attempt:
    """Classified result of one agent attempt before it becomes a StepDispatchOutcome."""

    status: AgentResponseStatus
    summary: str | None = None
    unfixable_reason: str | None = None
    touched_files: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """Return True for PROPOSED_PATCH and NO_OP."""
        return self.status in {AgentResponseStatus.PROPOSED_PATCH, AgentResponseStatus.NO_OP}


def execute_agent_step(
    step: StepDefinition,
    *,
    agent: ResolvedAgentSettings | None,
    sandbox_path: Path,
    sandbox_active: bool,
    on_output: Callable[[str, str], None] | None,
) -> StepDispatchOutcome:
    """Run one agent attempt through its resolved provider and return the classified dispatch outcome."""
    if not sandbox_active:
        return _to_outcome(_provider_error(SANDBOX_REQUIRED_MESSAGE), on_output)

    if agent is None:
        return _to_outcome(_provider_error(MISSING_SETTINGS_MESSAGE), on_output)

    if not (step.prompt or "").strip():
        return _to_outcome(_provider_error(BLANK_PROMPT_MESSAGE), on_output)

    request = _build_request(step, agent, sandbox_path)

    return _to_outcome(_run_provider(request, agent.provider), on_output)


def _provider_error(*diagnostics: str) -> _Attempt:
    """Build a provider_error attempt carrying the given diagnostics."""
    return _Attempt(status=AgentResponseStatus.PROVIDER_ERROR, diagnostics=list(diagnostics))


def _build_request(step: StepDefinition, agent: ResolvedAgentSettings, sandbox_path: Path) -> AgentRequest:
    """Build the direct-mode AgentRequest from the interpolated step and the resolved settings."""
    return AgentRequest(
        mode="direct",
        instruction=step.prompt or "",
        payload=None,
        sandbox_path=sandbox_path,
        timeout_seconds=step.timeout_seconds,
        model=agent.model,
        endpoint=agent.endpoint,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )


def _run_provider(request: AgentRequest, provider: str) -> _Attempt:
    """Resolve the adapter, call propose_fix once, and settle the response; any exception becomes provider_error."""
    try:
        adapter = get_agent_adapter(provider)
        response = adapter.propose_fix(request)

        return _settle(PROVIDERS[provider].kind, request, response)
    except Exception as exc:
        return _provider_error(f"Agent provider error: {exc}")


def _response_text(response: AgentResponse) -> str | None:
    """Return the provider summary, falling back to its raw result text."""
    return response.summary or response.raw_text


def _settle(kind: ProviderKind, request: AgentRequest, response: AgentResponse) -> _Attempt:
    """Classify a provider response: patches settle by provider kind, every other status maps straight through."""
    if response.status == AgentResponseStatus.PROPOSED_PATCH:
        if kind == ProviderKind.DIRECT_MUTATION:
            return _accept_direct_mutation(response)
        return _settle_diff_returning(request, response)

    return _Attempt(
        status=response.status,
        summary=_response_text(response),
        unfixable_reason=response.unfixable_reason,
        diagnostics=list(response.errors),
    )


def _settle_diff_returning(request: AgentRequest, response: AgentResponse) -> _Attempt:
    """Require a non-empty diff, validate it, apply it in the sandbox, and report PROPOSED_PATCH only after apply succeeds."""
    summary = _response_text(response)
    diff = response.unified_diff or ""

    if not diff.strip():
        diagnostics = ["Agent returned a proposed patch without a diff."]
    else:
        gate = validate_request_patch(request, diff)
        diagnostics = (
            _apply_in_sandbox(request.sandbox_path, diff)
            if gate.status == PatchApplyStatus.CHECKED_OK
            else list(gate.errors)
        )
        if not diagnostics:
            return _Attempt(
                status=AgentResponseStatus.PROPOSED_PATCH,
                summary=summary,
                touched_files=sorted(set(gate.touched_files)),
            )

    return _Attempt(status=AgentResponseStatus.PROVIDER_ERROR, summary=summary, diagnostics=diagnostics)


def _apply_in_sandbox(sandbox_path: Path, diff: str) -> list[str]:
    """Run apply_check then apply in the sandbox and return failure diagnostics (empty when the patch applied)."""
    try:
        returncode, _, stderr = GitRunner.apply_check(sandbox_path, diff)
        if returncode != 0:
            return [f"Patch does not apply cleanly: {stderr.strip()}"]

        returncode, _, stderr = GitRunner.apply(sandbox_path, diff)
        if returncode != 0:
            return [f"Patch application failed: {stderr.strip()}"]
    except GitError as exc:
        return [f"Patch application failed: {exc}"]

    return []


def _accept_direct_mutation(response: AgentResponse) -> _Attempt:
    """Record an accepted direct-mutation response and the paths its diff touched, without re-applying it."""
    paths, _, _ = GitDiffParser(response.unified_diff or "").parse()

    return _Attempt(
        status=AgentResponseStatus.PROPOSED_PATCH,
        summary=_response_text(response),
        touched_files=sorted(set(paths)),
    )


def _summary_line(attempt: _Attempt, *, status: AgentResponseStatus | None = None) -> str:
    """Serialize the attempt as exactly one compact JSON object followed by a newline."""
    summary = AgentStepSummary(
        status=status or attempt.status,
        summary=attempt.summary,
        unfixable_reason=attempt.unfixable_reason,
        touched_files=attempt.touched_files,
    )
    return summary.model_dump_json() + "\n"


def _failure_text(attempt: _Attempt) -> str:
    """Join the attempt's diagnostics, or name the unfixable reason or status when none were given."""
    if attempt.diagnostics:
        return "\n".join(attempt.diagnostics)

    if attempt.status == AgentResponseStatus.UNFIXABLE:
        return f"Agent reported the task unfixable: {attempt.unfixable_reason or 'no reason given'}"

    return f"Agent step ended with status '{attempt.status.value}'."


def _emit_summary(on_output: Callable[[str, str], None] | None, line: str) -> str | None:
    """Send the summary line through the output callback and return its error text, or None."""
    if on_output is None:
        return None

    try:
        on_output("stdout", line)
    except Exception as exc:
        return str(exc)

    return None


def _to_outcome(attempt: _Attempt, on_output: Callable[[str, str], None] | None) -> StepDispatchOutcome:
    """Convert an attempt into the completed or failed dispatch outcome, downgrading success when the callback fails."""
    line = _summary_line(attempt)
    callback_error = _emit_summary(on_output, line)

    if attempt.completed and callback_error is None:
        return StepDispatchOutcome(status="completed", exit_code=0, stdout=line, stderr="", error_message=None)

    if attempt.completed:
        message = f"Agent output callback error: {callback_error}"
        line = _summary_line(attempt, status=AgentResponseStatus.PROVIDER_ERROR)
    else:
        message = _failure_text(attempt)

    return StepDispatchOutcome(status="failed", exit_code=1, stdout=line, stderr=message, error_message=message)
