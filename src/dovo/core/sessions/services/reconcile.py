"""Reconciliation service detecting and recovering stale RUNNING session records."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

from dovo.common.diagnostic_log import DiagnosticLevel, diagnostic_hint_line, record_exception
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus, parse_timestamp
from dovo.core.sessions.models import ReconciliationResult

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


def _reconcile_stale_records(sessions_repo: SessionsRepository) -> list[SessionRecord]:
    """Inspect running session records and mark stale ones as failed."""
    reconciled: list[SessionRecord] = []
    for run in sessions_repo.list(status=SessionStatus.RUNNING):
        if is_run_stale(run):
            updated = sessions_repo.update_status(
                run.session_id,
                status=SessionStatus.FAILED,
                error_message=STALE_RUN_ERROR_MESSAGE,
            )
            if updated is not None:
                reconciled.append(updated)
    return reconciled


def reconcile_stale_sessions(db: SessionsRepository, path: Path | None = None) -> ReconciliationResult:
    """Inspect and reconcile stale RUNNING session records into FAILED status."""
    try:
        sessions_repo, root_dir = _resolve_sessions_repo_and_root(db, path)
    except ValueError:
        # No record: a detached repository has no workspace to reconcile.
        return ReconciliationResult()

    try:
        return _reconcile_under_lock(sessions_repo, root_dir)
    except LockTimeoutError:
        # No record: another dovo process holds the workspace lock; the next command reconciles.
        return ReconciliationResult()
    except Exception as exc:
        record = record_exception(
            exc, level=DiagnosticLevel.ERROR, operation="session.reconcile", context={"path": str(root_dir)}
        )
        return ReconciliationResult(warning=diagnostic_hint_line(record))


def _reconcile_under_lock(sessions_repo: SessionsRepository, root_dir: Path) -> ReconciliationResult:
    """Reconcile stale records while holding the workspace lock and return them with their warning."""
    with WorkspaceLock(RepositoryPaths.from_root(root_dir).lock_file):
        reconciled = _reconcile_stale_records(sessions_repo)

    return ReconciliationResult(reconciled=reconciled, warning=format_reconciliation_warning(reconciled))


def format_reconciliation_warning(reconciled: list[SessionRecord]) -> str | None:
    """Format non-intrusive warning message for reconciled stale sessions."""
    if not reconciled:
        return None

    if len(reconciled) == 1:
        return f"Reconciled 1 interrupted session (session_id: {reconciled[0].session_id})."

    session_ids = ", ".join(r.session_id for r in reconciled)
    return f"Reconciled {len(reconciled)} interrupted sessions ({session_ids})."
