"""Runtime failure orchestration helpers."""

from __future__ import annotations

from dovo.common.models import FailurePolicy, OnFailureSpec
from dovo.engine.executors.models import StepResult
from dovo.engine.models import StepAction

USER_CONTINUED_MARKER = "user continued after prompt_user"


def effective_terminal_policy(spec: OnFailureSpec) -> FailurePolicy:
    """Resolve the terminal escalation after step-local recovery finishes.

    When ``action == retry``, step execution already exhausted its local budget;
    the terminal policy is ``on_max_retries``. Otherwise the action itself is
    terminal (never ``retry``).
    """
    if spec.action == FailurePolicy.RETRY:
        return spec.on_max_retries
    return spec.action


def step_failure_diagnostic(result: StepResult) -> str:
    """Build a compact diagnostic string for a failed step result.

    @TODO: Check if exit code return is reachable
    """
    return result.error_message or result.stderr or f"exit code {result.exit_code}"


def mark_continued_after_prompt(result: StepResult) -> StepResult:
    """Rewrite a failed step as non-fatal after the user chooses continue."""
    diagnostic = step_failure_diagnostic(result)
    marker = f"{diagnostic} ({USER_CONTINUED_MARKER})" if diagnostic else USER_CONTINUED_MARKER
    return result.model_copy(update={"status": "ignored", "error_message": marker})


def failed_step_message(result: StepResult) -> str:
    """Format diagnostic message describing step failure."""
    detail = step_failure_diagnostic(result)
    return f"Step '{result.step_id}' failed: {detail}"


def resolve_terminal_action(
    policy: FailurePolicy,
    result: StepResult,
) -> tuple[StepAction, StepResult | None, str | None]:
    """Map a non-prompt terminal policy to ``(StepAction, result_to_record, error_message)``: CONTINUE records an ignored result; every other policy aborts with the failed result."""
    if policy == FailurePolicy.CONTINUE:
        return StepAction.CONTINUE, mark_continued_after_prompt(result), None
    return StepAction.ABORT, result, failed_step_message(result)
