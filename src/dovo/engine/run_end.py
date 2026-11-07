"""The run-end write: record how a started run ended on its session row and execution state."""

from __future__ import annotations

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import WorkspaceLock
from dovo.core.db import SessionStatus
from dovo.engine.executors.exceptions import EngineError
from dovo.engine.models import LifecycleUpdate, RunOutcome
from dovo.engine.state_models import ExecutionStateTree
from dovo.engine.state_store import SessionStateStore
from dovo.engine.writer import record_persist_failure


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
    None. A load or write failure is appended to warnings and its record to diagnostics; a lock timeout propagates.
    """
    lifecycle = LifecycleUpdate(
        run_status=outcome.status,
        error_message=outcome.errors[0] if outcome.errors else None,
        worktree_id=outcome.worktree_id,
        worktree_kept=worktree_kept,
    )
    with WorkspaceLock(store.paths.lock_file):
        try:
            _write_run_end(store, outcome.status, lifecycle, warnings, diagnostics)
        except Exception as exc:
            warnings.append(status_write_failure(exc, store.session_id, diagnostics))


def status_write_failure(exc: Exception, session_id: str, diagnostics: list[DiagnosticRecord]) -> str:
    """Add the failed run status write's record to diagnostics and return its warning, the message alone."""
    diagnostics.extend(record_persist_failure(exc, session_id))
    return f"Failed to update run status in database: {exc}"


def _write_run_end(
    store: SessionStateStore,
    status: SessionStatus,
    lifecycle: LifecycleUpdate,
    warnings: list[str],
    diagnostics: list[DiagnosticRecord],
) -> None:
    """Persist lifecycle through the state store, settling started nodes for status, or straight onto the row when it has no readable state."""
    state = _load_state(store, warnings, diagnostics)
    if state is None:
        store.sessions.update_status(
            store.session_id,
            status,
            error_message=lifecycle.error_message,
            worktree_id=lifecycle.worktree_id,
            worktree_kept=lifecycle.worktree_kept,
        )
        return

    state.settle_started_nodes(status)
    try:
        store.save(state, warnings, diagnostics, lifecycle)
    except EngineError as error:
        warnings.append(error.message)
        diagnostics.extend(record_persist_failure(error, store.session_id))


def _load_state(
    store: SessionStateStore, warnings: list[str], diagnostics: list[DiagnosticRecord]
) -> ExecutionStateTree | None:
    """Return the run's state, or None after appending the load failure to warnings and its record to diagnostics; a row without state stays quiet."""
    try:
        return store.load(warnings, diagnostics)
    except EngineError as error:
        if error.error_code is not ErrorCode.SESSION_STATE_MISSING:
            warnings.append(error.message)
            diagnostics.extend(record_persist_failure(error, store.session_id))
        return None
