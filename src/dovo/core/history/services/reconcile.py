"""Reconciliation service detecting and recovering stale RUNNING session records."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.lock import WorkspaceLock
from dovo.core.db import RunRecord, RunsRepository, RunStatus
from dovo.core.history.models import ReconciliationResult

STALE_RUN_ERROR_MESSAGE = "Session interrupted by abnormal process termination"


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
    """Retrieve UTC creation/start timestamp of process with pid if accessible."""
    if pid <= 0:
        return None

    try:
        import importlib

        psutil_mod = importlib.import_module("psutil")
        proc = psutil_mod.Process(pid)
        return datetime.fromtimestamp(proc.create_time(), tz=UTC)
    except Exception:
        pass

    proc_dir = Path(f"/proc/{pid}")
    if proc_dir.is_dir():
        try:
            stat_info = proc_dir.stat()
            return datetime.fromtimestamp(stat_info.st_ctime, tz=UTC)
        except OSError:
            return None

    return None


def _parse_timestamp(timestamp_str: str) -> datetime | None:
    """Parse UTC date string (%Y-%m-%d %H:%M:%S or ISO-8601) into timezone-aware datetime."""
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(timestamp_str)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _is_pid_reused(pid: int, session_start: datetime | None, current_pid: int) -> bool:
    """Check if an alive PID was recycled after session_start."""
    if session_start is None:
        return False

    if pid == current_pid:
        current_start = get_process_start_time(current_pid)
        return bool(current_start and current_start > session_start + timedelta(seconds=1))

    proc_start = get_process_start_time(pid)
    return bool(proc_start and proc_start > session_start + timedelta(seconds=1))


def is_run_stale(run: RunRecord, current_pid: int | None = None) -> bool:
    """Determine whether a RUNNING run record represents an interrupted or dead session."""
    if run.status != RunStatus.RUNNING:
        return False

    if run.pid is None or not is_pid_alive(run.pid):
        return True

    eff_current_pid = current_pid if current_pid is not None else os.getpid()
    session_start = _parse_timestamp(run.started_at)
    return _is_pid_reused(run.pid, session_start, eff_current_pid)


def _resolve_runs_repo_and_root(
    runs_repo: RunsRepository,
    path: Path | None,
) -> tuple[RunsRepository, Path]:
    """Resolve RunsRepository and root path from input repository target."""
    root = path or runs_repo.path
    if root is None:
        raise ValueError("A workspace path must be provided when passing a detached RunsRepository.")
    return runs_repo, root


def _reconcile_stale_records(runs_repo: RunsRepository) -> list[RunRecord]:
    """Inspect running run records and mark stale ones as failed."""
    reconciled: list[RunRecord] = []
    for run in runs_repo.list(status=RunStatus.RUNNING):
        if is_run_stale(run):
            updated = runs_repo.update_status(
                run.session_id,
                status=RunStatus.FAILED,
                error_message=STALE_RUN_ERROR_MESSAGE,
            )
            if updated is not None:
                reconciled.append(updated)
    return reconciled


def reconcile_stale_runs(db: RunsRepository, path: Path | None = None) -> ReconciliationResult:
    """Inspect and reconcile stale RUNNING run records into FAILED status."""
    try:
        runs_repo, root_dir = _resolve_runs_repo_and_root(db, path)
        with WorkspaceLock(RepositoryPaths.from_root(root_dir).lock_file):
            reconciled = _reconcile_stale_records(runs_repo)

        warning = format_reconciliation_warning(reconciled)
        return ReconciliationResult(reconciled=reconciled, warning=warning)
    except Exception:
        # Best-effort reconciliation failure should not crash calling workflows.
        return ReconciliationResult()


def format_reconciliation_warning(reconciled: list[RunRecord]) -> str | None:
    """Format non-intrusive warning message for reconciled stale runs."""
    if not reconciled:
        return None

    if len(reconciled) == 1:
        return f"Reconciled 1 interrupted session (session_id: {reconciled[0].session_id})."

    session_ids = ", ".join(r.session_id for r in reconciled)
    return f"Reconciled {len(reconciled)} interrupted sessions ({session_ids})."
