"""Reconciliation service detecting and recovering stale RUNNING session records."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

from dovo.common.diagnostic_log import DiagnosticLevel, record_exception
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus, WorktreesRepository, parse_timestamp
from dovo.core.sessions.models import Reconciliation

STALE_RUN_ERROR_MESSAGE = "Session interrupted by abnormal process termination"
PID_START_TOLERANCE = timedelta(seconds=1)


def is_pid_alive(pid: int) -> bool:
    """Check if process with pid is alive on the local OS."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user
        return True
    except OSError:
        return False


def get_process_start_time(pid: int) -> datetime | None:
    """Return the UTC creation time of process pid, or None when it cannot be read."""
    if pid <= 0:
        return None

    try:
        return datetime.fromtimestamp(psutil.Process(pid).create_time(), tz=UTC)
    except psutil.Error:
        return None


def current_process_started_at() -> str | None:
    """Return this process's OS creation time as ISO-8601 UTC with microseconds, or None when it cannot be read."""
    created = get_process_start_time(os.getpid())
    return created.isoformat(timespec="microseconds") if created is not None else None


def _is_pid_reused(pid: int, pid_started_at: str | None) -> bool:
    """Check whether live pid belongs to a process other than the one created at pid_started_at."""
    recorded = parse_timestamp(pid_started_at)
    if recorded is None:
        return False

    actual = get_process_start_time(pid)
    return actual is not None and abs(actual - recorded) > PID_START_TOLERANCE


def is_run_stale(run: SessionRecord) -> bool:
    """Determine whether a RUNNING session record represents an interrupted or dead session."""
    if run.status != SessionStatus.RUNNING:
        return False

    if run.pid is None or not is_pid_alive(run.pid):
        return True

    return _is_pid_reused(run.pid, run.pid_started_at)


def _resolve_sessions_repo_and_root(
    sessions_repo: SessionsRepository,
    path: Path | None,
) -> tuple[SessionsRepository, Path]:
    """Resolve SessionsRepository and root path from input repository target."""
    root = path or sessions_repo.path
    if root is None:
        raise ValueError("A workspace path must be provided when passing a detached SessionsRepository.")
    return sessions_repo, root


def _live_session_ids() -> set[str]:
    """Return the DOVO_SESSION_ID of every live process; a process whose environment cannot be read is not counted."""
    session_ids: set[str] = set()
    try:
        for process in psutil.process_iter(["environ"], ad_value=None):
            environ = process.info["environ"]
            if environ is not None and (session_id := environ.get("DOVO_SESSION_ID")):
                session_ids.add(session_id)
    except psutil.Error:
        # A process table that cannot be walked counts no further process.
        pass
    return session_ids


def _worktree_present(run: SessionRecord, sessions_repo: SessionsRepository) -> bool:
    """Check whether the run records a worktree row whose recorded directory exists on disk."""
    if run.worktree_id is None:
        return False

    worktrees = WorktreesRepository(
        db_path=sessions_repo.db_path, project_id=sessions_repo.project_id, db_engine=sessions_repo.db_engine
    )
    worktree = worktrees.get(run.worktree_id)
    return worktree is not None and worktree.worktree_path.exists()


def _resumable(run: SessionRecord, sessions_repo: SessionsRepository, live_session_ids: set[str]) -> bool:
    """Check whether a stale run has saved state, its worktree if it used one, and no live process."""
    if run.execution_state_json is None or run.session_id in live_session_ids:
        return False

    return not run.use_worktree or _worktree_present(run, sessions_repo)


def _reconcile_stale_records(sessions_repo: SessionsRepository) -> tuple[list[SessionRecord], list[SessionRecord]]:
    """Mark each stale running record paused when it can resume and failed otherwise; return the paused and failed rows."""
    stale = [run for run in sessions_repo.list(status=SessionStatus.RUNNING) if is_run_stale(run)]
    has_state = any(run.execution_state_json is not None for run in stale)
    live_session_ids = _live_session_ids() if has_state else set()

    paused: list[SessionRecord] = []
    failed: list[SessionRecord] = []
    for run in stale:
        resumable = _resumable(run, sessions_repo, live_session_ids)
        updated = sessions_repo.update_status(
            run.session_id,
            status=SessionStatus.PAUSED if resumable else SessionStatus.FAILED,
            error_message=None if resumable else STALE_RUN_ERROR_MESSAGE,
        )
        if updated is not None:
            (paused if resumable else failed).append(updated)
    return paused, failed


def reconcile_stale_sessions(db: SessionsRepository, path: Path | None = None) -> Reconciliation:
    """Mark stale RUNNING session records PAUSED or FAILED; on an unexpected failure, skip them and return its record.

    A stale record is paused when it has saved execution state, its worktree is present if it used
    one, and no live process carries its session id; every other stale record is marked failed.

    Raises:
        LockTimeoutError: If another process holds the workspace lock.
    """
    try:
        sessions_repo, root_dir = _resolve_sessions_repo_and_root(db, path)
    except ValueError:
        # No record: a detached repository has no workspace to reconcile.
        return Reconciliation()

    try:
        return _reconcile_under_lock(sessions_repo, root_dir)
    except LockTimeoutError:
        # Propagates to the top-level handler, like every lock timeout (#1135).
        raise
    except Exception as exc:
        record = record_exception(
            exc, level=DiagnosticLevel.ERROR, operation="sessions.reconcile", context={"path": str(root_dir)}
        )
        return Reconciliation(diagnostic=record)


def _reconcile_under_lock(sessions_repo: SessionsRepository, root_dir: Path) -> Reconciliation:
    """Reconcile stale records while holding the workspace lock and return the paused and failed rows with their warning."""
    with WorkspaceLock(RepositoryPaths.from_root(root_dir).lock_file):
        paused, failed = _reconcile_stale_records(sessions_repo)

    return Reconciliation(
        paused=tuple(paused), failed=tuple(failed), warning=format_reconciliation_warning(paused, failed)
    )


def _paused_sentence(paused: Sequence[SessionRecord]) -> str | None:
    """Format the sentence naming the paused sessions and how to continue them."""
    if not paused:
        return None

    if len(paused) == 1:
        return "Paused 1 interrupted session; continue it with 'dovo resume <session_id>'."

    session_ids = ", ".join(r.session_id for r in paused)
    return f"Paused {len(paused)} interrupted sessions ({session_ids}); continue each with 'dovo resume <session_id>'."


def _failed_sentence(failed: Sequence[SessionRecord]) -> str | None:
    """Format the sentence naming the sessions marked failed."""
    if not failed:
        return None

    if len(failed) == 1:
        return f"Marked 1 interrupted session failed (session_id: {failed[0].session_id})."

    session_ids = ", ".join(r.session_id for r in failed)
    return f"Marked {len(failed)} interrupted sessions failed ({session_ids})."


def format_reconciliation_warning(paused: Sequence[SessionRecord], failed: Sequence[SessionRecord]) -> str | None:
    """Format the reconciliation warning, the paused sentence before the failed one, or None when both are empty."""
    sentences = [sentence for sentence in (_paused_sentence(paused), _failed_sentence(failed)) if sentence is not None]
    return " ".join(sentences) or None
