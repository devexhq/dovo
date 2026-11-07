"""Closes the interrupted steps of a stale session that reconciliation marks failed."""

from __future__ import annotations

from pydantic import ValidationError

from dovo.core.db import SessionRecord
from dovo.core.sessions import StepStateCloser
from dovo.engine.executors.models import StepRecord
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionPlanNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
    with_descendants,
)

INTERRUPTED_ATTEMPT_MESSAGE = "Step attempt was interrupted before it recorded a result."
INTERRUPTED_ATTEMPT_EXIT_CODE = 1


class InterruptedStepCloser(StepStateCloser):
    """Fails every RUNNING node of a stale session's saved state, closing each open last attempt as interrupted."""

    def close(self, record: SessionRecord) -> str | None:
        """Return record's state with its RUNNING nodes failed, at the row's revision + 1, or None when nothing changed.

        A RUNNING step whose last attempt has no result gets that attempt closed failed with exit code 1 and the
        interrupted-attempt message. Nodes in any other state, PAUSED and PENDING included, are left as they were.
        """
        state = _parse_state(record)
        if state is None:
            return None

        changed = [_fail_running(item) for node in state.nodes for item in with_descendants(node)]
        if not any(changed):
            return None

        state.revision += 1
        return state.model_dump_json()


def _parse_state(record: SessionRecord) -> ExecutionStateTree | None:
    """Return record's state when it parses and its revision matches the row's, else None."""
    if record.execution_state_json is None:
        return None
    try:
        state = ExecutionStateTree.model_validate_json(record.execution_state_json)
    except ValidationError:
        return None
    return state if state.revision == record.execution_state_revision else None


def _fail_running(node: ExecutionPlanNode | ExecutionIterationRecord) -> bool:
    """Set a RUNNING node FAILED, closing a step's open last attempt; return whether it changed."""
    if node.state is not NodeState.RUNNING:
        return False
    if isinstance(node, ExecutionLeafNode) and node.attempts and node.attempts[-1].result is None:
        node.attempts[-1] = _closed_attempt(node.id, node.attempts[-1])
    node.state = NodeState.FAILED
    return True


def _closed_attempt(step_id: str, attempt: StepAttemptRecord) -> StepAttemptRecord:
    """Return attempt with its span closed now and a failed, interrupted result."""
    span = attempt.span.close()
    result = StepRecord(
        step_id=step_id,
        status="failed",
        exit_code=INTERRUPTED_ATTEMPT_EXIT_CODE,
        stdout="",
        stderr="",
        duration_seconds=span.duration_seconds or 0.0,
        error_message=INTERRUPTED_ATTEMPT_MESSAGE,
    )
    return attempt.model_copy(update={"span": span, "result": result})
