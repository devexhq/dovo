"""Agent step execution: resolved provider invocation, worktree application, and the stdout summary."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from dovo.core.agents import AgentAttempt, AgentResponseStatus, ResolvedAgentSettings, run_direct_attempt
from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import AgentStepRunner, AgentStepSummary, OutputCallback, StepDispatchOutcome

WORKTREE_REQUIRED_MESSAGE = (
    "Agent steps require an active git worktree.\nFix: remove --no-worktree and run the blueprint with a worktree."
)
MISSING_SETTINGS_MESSAGE = (
    "Agent step has no resolved agent settings "
    "(agent.provider, agent.model, agent.endpoint, agent.temperature, agent.max_tokens)."
)
BLANK_PROMPT_MESSAGE = "Agent step prompt is blank after interpolation."

# Step outcome code per agent status; 200 is unused and 204 is reserved for a future blocked status.
AGENT_OUTCOME_EXIT_CODES: Final[Mapping[AgentResponseStatus, int]] = MappingProxyType(
    {
        AgentResponseStatus.PROPOSED_PATCH: 0,
        AgentResponseStatus.NO_OP: 0,
        AgentResponseStatus.UNFIXABLE: 201,
        AgentResponseStatus.TIMEOUT: 202,
        AgentResponseStatus.PROVIDER_ERROR: 203,
    }
)


def execute_agent_step(
    step: StepDefinition,
    *,
    agent: ResolvedAgentSettings | None,
    worktree_path: Path,
    worktree_active: bool,
    on_output: OutputCallback | None,
) -> StepDispatchOutcome:
    """Run one agent attempt through its resolved provider and return the classified dispatch outcome."""
    if not worktree_active:
        return _to_outcome(_provider_error(WORKTREE_REQUIRED_MESSAGE), on_output)

    if agent is None:
        return _to_outcome(_provider_error(MISSING_SETTINGS_MESSAGE), on_output)

    if not (step.prompt or "").strip():
        return _to_outcome(_provider_error(BLANK_PROMPT_MESSAGE), on_output)

    attempt = run_direct_attempt(
        instruction=step.prompt or "",
        settings=agent,
        worktree_path=worktree_path,
        timeout_seconds=step.timeout_seconds,
    )

    return _to_outcome(attempt, on_output)


def build_agent_step_runner(agent: ResolvedAgentSettings | None, worktree_active: bool) -> AgentStepRunner:
    """Return a runner closing over the run's resolved agent settings and worktree state, delegating to execute_agent_step."""

    def _run(step: StepDefinition, worktree_path: Path, on_output: OutputCallback | None) -> StepDispatchOutcome:
        return execute_agent_step(
            step, agent=agent, worktree_path=worktree_path, worktree_active=worktree_active, on_output=on_output
        )

    return _run


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


def _emit_summary(on_output: OutputCallback | None, line: str) -> str | None:
    """Send the summary line through the output callback and return its error text, or None."""
    if on_output is None:
        return None

    try:
        on_output("stdout", line)
    except Exception as exc:
        return str(exc)

    return None


def _to_outcome(attempt: AgentAttempt, on_output: OutputCallback | None) -> StepDispatchOutcome:
    """Convert an attempt into the completed or failed dispatch outcome, downgrading success when the callback fails."""
    line = _summary_line(attempt)
    callback_error = _emit_summary(on_output, line)

    if not attempt.completed:
        return _failed_outcome(attempt.status, line, _failure_text(attempt))

    if callback_error is not None:
        downgraded = _summary_line(attempt, status=AgentResponseStatus.PROVIDER_ERROR)
        return _failed_outcome(
            AgentResponseStatus.PROVIDER_ERROR, downgraded, f"Agent output callback error: {callback_error}"
        )

    return StepDispatchOutcome(
        status="completed",
        exit_code=AGENT_OUTCOME_EXIT_CODES[attempt.status],
        stdout=line,
        stderr="",
        error_message=None,
    )


def _failed_outcome(status: AgentResponseStatus, line: str, message: str) -> StepDispatchOutcome:
    """Build the failed dispatch outcome carrying the mapped exit code for status, the summary line, and the diagnostic."""
    return StepDispatchOutcome(
        status="failed", exit_code=AGENT_OUTCOME_EXIT_CODES[status], stdout=line, stderr=message, error_message=message
    )
