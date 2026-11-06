"""The run-end write: record how a started run ended on its session row and execution state."""

from __future__ import annotations

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import WorkspaceLock
from dovo.core.db import SessionStatus
from dovo.engine.executors.exceptions import EngineError
from dovo.engine.models import LifecycleUpdate, RunOutcome
from dovo.engine.state_models import ExecutionStateTree, NodeState
from dovo.engine.state_store import SessionStateStore

_SETTLED_NODE_STATES = {SessionStatus.CANCELLED: NodeState.CANCELLED, SessionStatus.FAILED: NodeState.FAILED}


def record_run_end(
    store: SessionStateStore,
    outcome: RunOutcome,
    warnings: list[str],
    diagnostics: list[DiagnosticRecord],
    *,
    worktree_kept: bool | None,
) -> None:
    """Under the workspace lock, settle a cancelled or failed run's started nodes and write the outcome's status, error and worktree id, and worktree_kept unless it is None.

    Only the workspace close knows whether the worktree was kept: a write before it passes True for a worktree the
    run has, so an interrupted or timed-out close leaves it recorded kept, and a write that did not close passes
    None. A write failure is appended to warnings and its record to diagnostics; a lock timeout propagates.
    """
    lifecycle = LifecycleUpdate(
        run_status=outcome.status,
        error_message=outcome.errors[0] if outcome.errors else None,
        worktree_id=outcome.worktree_id,
        worktree_kept=worktree_kept,
    )
    with WorkspaceLock(store.paths.lock_file):
        try:
            _write_run_end(store, outcome.status, lifecycle, warnings)
        except Exception as exc:
            warnings.append(status_write_failure(exc, store.session_id, diagnostics))


def record_persist_failure(exc: Exception, session_id: str) -> list[DiagnosticRecord]:
    """Record exc at error level under engine.persist and return its record as a diagnostics list."""
    return error_diagnostics(
        record_exception(
            exc, level=DiagnosticLevel.ERROR, operation="engine.persist", context={"session_id": session_id}
        )
    )


def status_write_failure(exc: Exception, session_id: str, diagnostics: list[DiagnosticRecord]) -> str:
    """Add the failed run status write's record to diagnostics and return its warning, the message alone."""
    diagnostics.extend(record_persist_failure(exc, session_id))
    return f"Failed to update run status in database: {exc}"


def _write_run_end(
    store: SessionStateStore, status: SessionStatus, lifecycle: LifecycleUpdate, warnings: list[str]
) -> None:
    """Persist lifecycle through the state store, settling started nodes for status, or straight onto the row when it has no readable state."""
    state = _load_state(store, warnings)
    if state is None:
        store.sessions.update_status(
            store.session_id,
            status,
            error_message=lifecycle.error_message,
            worktree_id=lifecycle.worktree_id,
            worktree_kept=lifecycle.worktree_kept,
        )
        return

    node_state = _SETTLED_NODE_STATES.get(status)
    if node_state is not None:
        state.settle_started_nodes(node_state)
    try:
        store.save(state, warnings, lifecycle)
    except EngineError as error:
        warnings.append(error.message)


def _load_state(store: SessionStateStore, warnings: list[str]) -> ExecutionStateTree | None:
    """Return the run's state, or None after appending the load failure to warnings; a row without state stays quiet."""
    try:
        return store.load(warnings)
    except EngineError as error:
        if error.error_code is not ErrorCode.SESSION_STATE_MISSING:
            warnings.append(error.message)
        return None
