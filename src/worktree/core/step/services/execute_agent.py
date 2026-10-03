"""Agent step execution: resolved provider invocation, sandbox application, and the stdout summary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from worktree.core.agents import AgentAttempt, AgentResponseStatus, ResolvedAgentSettings, run_direct_attempt
from worktree.core.step.models import AgentStepSummary, StepDefinition, StepDispatchOutcome

SANDBOX_REQUIRED_MESSAGE = "Agent steps require an active Worktree Git sandbox.\nFix: remove --no-sandbox and run the blueprint with a sandbox."
MISSING_SETTINGS_MESSAGE = (
    "Agent step has no resolved agent settings "
    "(agent.provider, agent.model, agent.endpoint, agent.temperature, agent.max_tokens)."
)
BLANK_PROMPT_MESSAGE = "Agent step prompt is blank after interpolation."


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

    attempt = run_direct_attempt(
        instruction=step.prompt or "",
        settings=agent,
        sandbox_path=sandbox_path,
        timeout_seconds=step.timeout_seconds,
    )

    return _to_outcome(attempt, on_output)


def _provider_error(*diagnostics: str) -> AgentAttempt:
    """Build a provider_error attempt carrying the given diagnostics."""
    return AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, diagnostics=list(diagnostics))


def _summary_line(attempt: AgentAttempt, *, status: AgentResponseStatus | None = None) -> str:
    """Serialize the attempt as exactly one compact JSON object followed by a newline."""
    summary = AgentStepSummary(
        status=status or attempt.status,
        summary=attempt.summary,
        unfixable_reason=attempt.unfixable_reason,
        touched_files=attempt.touched_files,
    )
    return summary.model_dump_json() + "\n"


def _failure_text(attempt: AgentAttempt) -> str:
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


def _to_outcome(attempt: AgentAttempt, on_output: Callable[[str, str], None] | None) -> StepDispatchOutcome:
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
