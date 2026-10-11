"""Read-only step summaries derived from a session's persisted execution state."""

from __future__ import annotations

from pydantic import ValidationError

from dovo.common.redact import SecretRedactor
from dovo.core.db import SessionRecord
from dovo.core.sessions import StepRunSummary
from dovo.engine.models import SessionStepsResult
from dovo.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    StepAttemptRecord,
)

NO_STEP_DATA_WARNING = "No step data recorded for this session."


def summarize_session_steps(record: SessionRecord, redactor: SecretRedactor) -> SessionStepsResult:
    """Return one redacted StepRunSummary per execution node in plan order, or a warning when the state is absent or unreadable."""
    if record.execution_state_json is None:
        return SessionStepsResult(warnings=[NO_STEP_DATA_WARNING])

    try:
        state = ExecutionStateTree.model_validate_json(record.execution_state_json)
    except ValidationError as validation_error:
        reason = validation_error.errors(include_url=False, include_input=False)[0]["msg"]
        return SessionStepsResult(warnings=[f"Step data could not be read: {reason}"])

    steps: list[StepRunSummary] = []
    for node in state.nodes:
        if isinstance(node, ExecutionLoopNode):
            steps.extend(_loop_rows(node, redactor))
        else:
            steps.append(_leaf_summary(node, redactor))

    return SessionStepsResult(steps=steps)


def _loop_rows(loop: ExecutionLoopNode, redactor: SecretRedactor) -> list[StepRunSummary]:
    """Build the loop row followed by one row per body step of each iteration, in order."""
    rows = [_loop_summary(loop)]
    for iteration in loop.iterations:
        rows.extend(
            _leaf_summary(leaf, redactor, loop_id=loop.id, iteration=iteration.number) for leaf in iteration.steps
        )

    return rows


def _leaf_summary(
    leaf: ExecutionLeafNode, redactor: SecretRedactor, *, loop_id: str | None = None, iteration: int | None = None
) -> StepRunSummary:
    """Build the kind="step" row for one leaf node."""
    results = [attempt.result for attempt in leaf.attempts if attempt.result is not None]

    return StepRunSummary(
        kind="step",
        step_id=leaf.id,
        name=leaf.name,
        state=leaf.state.value,
        attempts=len(leaf.attempts),
        exit_code=results[-1].exit_code if results else None,
        duration_seconds=sum(result.duration_seconds for result in results) if results else None,
        error_message=_last_error_message(leaf.attempts, redactor),
        loop_id=loop_id,
        iteration=iteration,
    )


def _loop_summary(loop: ExecutionLoopNode) -> StepRunSummary:
    """Build the kind="loop" row: iterations set, attempt-derived fields None."""
    return StepRunSummary(kind="loop", step_id=loop.id, state=loop.state.value, iterations=len(loop.iterations))


def _last_error_message(attempts: list[StepAttemptRecord], redactor: SecretRedactor) -> str | None:
    """Return the redacted error_message of the last attempt, None when it has no result or no message."""
    if not attempts or attempts[-1].result is None or attempts[-1].result.error_message is None:
        return None

    return redactor.redact_text(attempts[-1].result.error_message)
