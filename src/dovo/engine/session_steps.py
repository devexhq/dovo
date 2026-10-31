"""Read-only step summaries derived from a session's persisted execution state."""

from __future__ import annotations

from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.redact import SecretRedactor
from dovo.core.db import SessionRecord
from dovo.core.sessions import StepRunSummary
from dovo.engine.exceptions import EngineError
from dovo.engine.state_models import (
    ExecutionIfNode,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    StepAttemptRecord,
)

NO_STEP_DATA_WARNING = "No step data recorded for this session."


def summarize_session_steps(record: SessionRecord, redactor: SecretRedactor) -> list[StepRunSummary]:
    """Return one redacted StepRunSummary per execution node in plan order.

    Raises:
        EngineError: SESSION_STATE_MISSING when no state is recorded, SESSION_STATE_CORRUPT when it cannot be parsed.
    """
    if record.execution_state_json is None:
        raise EngineError(ErrorCode.SESSION_STATE_MISSING, NO_STEP_DATA_WARNING)

    try:
        state = ExecutionStateTree.model_validate_json(record.execution_state_json)
    except ValidationError as validation_error:
        reason = validation_error.errors(include_url=False, include_input=False)[0]["msg"]
        raise EngineError(
            ErrorCode.SESSION_STATE_CORRUPT, f"Step data could not be read: {reason}"
        ) from validation_error

    steps: list[StepRunSummary] = []
    for node in state.nodes:
        if isinstance(node, ExecutionLoopNode):
            steps.extend(_loop_rows(node, redactor))
        elif isinstance(node, ExecutionIfNode):
            steps.extend(_if_rows(node, redactor))
        else:
            steps.append(_leaf_summary(node, redactor))

    return steps


def _if_rows(block: ExecutionIfNode, redactor: SecretRedactor) -> list[StepRunSummary]:
    """Build the if-block row, with the branch it took, followed by one row per step of that branch, in order."""
    row = StepRunSummary(
        kind="if",
        step_id=block.id,
        name=block.name,
        state=block.state.value,
        branch=None if block.branch is None else block.branch.value,
    )
    return [row, *(_leaf_summary(leaf, redactor, if_id=block.id) for leaf in block.steps)]


def _loop_rows(loop: ExecutionLoopNode, redactor: SecretRedactor) -> list[StepRunSummary]:
    """Build the loop row followed by one row per body step of each iteration, in order; a skipped loop has no body rows."""
    rows = [_loop_summary(loop)]
    if loop.skip is not None:
        return rows
    for iteration in loop.iterations:
        rows.extend(
            _leaf_summary(leaf, redactor, loop_id=loop.id, iteration=iteration.number) for leaf in iteration.steps
        )

    return rows


def _leaf_summary(
    leaf: ExecutionLeafNode,
    redactor: SecretRedactor,
    *,
    loop_id: str | None = None,
    iteration: int | None = None,
    if_id: str | None = None,
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
        rejected_patch_path=_last_rejected_patch_path(leaf.attempts, redactor),
        loop_id=loop_id,
        iteration=iteration,
        skip_cause=None if leaf.skip is None else leaf.skip.cause.value,
        if_id=if_id,
    )


def _loop_summary(loop: ExecutionLoopNode) -> StepRunSummary:
    """Build the kind="loop" row: iterations set (none for a skipped loop), attempt-derived fields None."""
    return StepRunSummary(
        kind="loop",
        step_id=loop.id,
        state=loop.state.value,
        iterations=0 if loop.skip is not None else len(loop.iterations),
        skip_cause=None if loop.skip is None else loop.skip.cause.value,
    )


def _last_error_message(attempts: list[StepAttemptRecord], redactor: SecretRedactor) -> str | None:
    """Return the redacted error_message of the last attempt, None when it has no result or no message."""
    if not attempts or attempts[-1].result is None or attempts[-1].result.error_message is None:
        return None

    return redactor.redact_text(attempts[-1].result.error_message)


def _last_rejected_patch_path(attempts: list[StepAttemptRecord], redactor: SecretRedactor) -> str | None:
    """Return the redacted rejected_patch_path of the last attempt, None when it has no result or no path."""
    if not attempts or attempts[-1].result is None or attempts[-1].result.rejected_patch_path is None:
        return None

    return redactor.redact_text(attempts[-1].result.rejected_patch_path)
