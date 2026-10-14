"""Tier 1 domain tests for stale-run reconciliation."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.sessions import ReconciliationResult
from dovo.core.sessions.services.reconcile import (
    STALE_RUN_ERROR_MESSAGE,
    format_reconciliation_warning,
    get_process_start_time,
    is_pid_alive,
    is_run_stale,
    reconcile_stale_sessions,
)
from tests.harness.workspace_paths import initialized_workspace_paths

DEAD_PID = 4242


def _sessions_for(root: Path) -> SessionsRepository:
    """Build the SessionsRepository for the workspace rooted at root."""
    paths = initialized_workspace_paths(root)
    return SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)


def _kill_reports_no_such_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every pid look dead to is_pid_alive."""

    def _kill(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("dovo.core.sessions.services.reconcile.os.kill", _kill)


@pytest.fixture
def child_process() -> Generator[subprocess.Popen[bytes]]:
    """Live child process that is terminated and reaped at teardown."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield child
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


class IsPidAliveTests:
    @pytest.mark.parametrize(
        ("pid", "kill_error", "expected"),
        [
            (0, None, False),
            (-1, None, False),
            (DEAD_PID, ProcessLookupError(), False),
            (DEAD_PID, PermissionError(), True),
            (DEAD_PID, OSError(), False),
            (DEAD_PID, None, True),
        ],
    )
    def test_is_pid_alive_maps_os_kill_outcome(
        self, monkeypatch: pytest.MonkeyPatch, pid: int, kill_error: OSError | None, expected: bool
    ) -> None:
        """[tier-1/unit] is_pid_alive: pid <= 0 -> False without os.kill; ProcessLookupError -> False; PermissionError -> True; other OSError -> False; os.kill success -> True."""
        kill_calls: list[int] = []

        def _kill(killed_pid: int, _signal: int) -> None:
            kill_calls.append(killed_pid)
            if kill_error is not None:
                raise kill_error

        monkeypatch.setattr("dovo.core.sessions.services.reconcile.os.kill", _kill)

        assert is_pid_alive(pid) is expected
        assert kill_calls == ([] if pid <= 0 else [pid])


class GetProcessStartTimeTests:
    def test_non_positive_pid_returns_none(self) -> None:
        """[tier-1/unit] get_process_start_time: pid 0 returns None."""
        assert get_process_start_time(0) is None

    def test_live_child_start_time_is_utc_between_test_process_start_and_now(
        self, child_process: subprocess.Popen[bytes]
    ) -> None:
        """[tier-1/unit] get_process_start_time: live child pid returns tzinfo == UTC with get_process_start_time(os.getpid()) <= result <= datetime.now(UTC)."""
        test_process_start = get_process_start_time(os.getpid())

        child_start = get_process_start_time(child_process.pid)

        assert test_process_start is not None
        assert child_start is not None
        assert child_start.tzinfo == UTC
        assert test_process_start <= child_start <= datetime.now(UTC)

    def test_reaped_child_pid_returns_none(self, child_process: subprocess.Popen[bytes]) -> None:
        """[tier-1/unit] get_process_start_time: pid of a terminated and waited child returns None (psutil.NoSuchProcess)."""
        child_process.terminate()
        child_process.wait(timeout=10)

        assert get_process_start_time(child_process.pid) is None


class IsRunStaleTests:
    @pytest.mark.parametrize("status", [SessionStatus.COMPLETED, SessionStatus.FAILED])
    def test_non_running_status_is_not_stale(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, status: SessionStatus
    ) -> None:
        """[tier-1/integration] is_run_stale: a non-RUNNING row returns False even with a dead pid."""
        _kill_reports_no_such_process(monkeypatch)
        run = _sessions_for(isolated_workspace).create(
            "session-1", "blueprint", "blueprint", status=status, pid=DEAD_PID
        )

        assert is_run_stale(run) is False

    def test_running_row_without_pid_is_stale(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] is_run_stale: RUNNING row with pid None returns True."""
        run = _sessions_for(isolated_workspace).create("session-1", "blueprint", "blueprint", pid=None)

        assert is_run_stale(run) is True

    @pytest.mark.parametrize(
        ("session_offset_seconds", "expected"),
        [
            pytest.param(10, False, id="session-after-process"),
            pytest.param(-10, True, id="session-before-process"),
        ],
    )
    def test_running_child_is_stale_only_when_process_started_after_session_start(
        self,
        isolated_workspace: Path,
        child_process: subprocess.Popen[bytes],
        session_offset_seconds: int,
        expected: bool,
    ) -> None:
        """[tier-1/integration] is_run_stale: RUNNING row for a live child with started_at = child start +10s returns False; with started_at = child start -10s returns True."""
        child_start = get_process_start_time(child_process.pid)
        assert child_start is not None
        started_at = (child_start + timedelta(seconds=session_offset_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        run = _sessions_for(isolated_workspace).create("session-1", "blueprint", "blueprint", pid=child_process.pid)
        run = run.model_copy(update={"started_at": started_at})

        assert is_run_stale(run) is expected

    def test_unparseable_started_at_with_live_pid_is_not_stale(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] is_run_stale: RUNNING row owned by os.getpid() with started_at 'not-a-timestamp' returns False."""
        run = _sessions_for(isolated_workspace).create("session-1", "blueprint", "blueprint", pid=os.getpid())
        run = run.model_copy(update={"started_at": "not-a-timestamp"})

        assert is_run_stale(run) is False


class FormatReconciliationWarningTests:
    def test_empty_list_returns_none(self) -> None:
        """[tier-1/unit] format_reconciliation_warning: [] returns None."""
        assert format_reconciliation_warning([]) is None

    @pytest.mark.parametrize(
        ("session_ids", "expected"),
        [
            pytest.param(["a"], "Reconciled 1 interrupted session (session_id: a).", id="single"),
            pytest.param(["a", "b"], "Reconciled 2 interrupted sessions (a, b).", id="multiple"),
        ],
    )
    def test_records_name_their_sessions(self, isolated_workspace: Path, session_ids: list[str], expected: str) -> None:
        """[tier-1/integration] format_reconciliation_warning: one record 'a' returns 'Reconciled 1 interrupted session (session_id: a).'; records 'a','b' return 'Reconciled 2 interrupted sessions (a, b).'."""
        sessions = _sessions_for(isolated_workspace)
        records = [sessions.create(session_id, "blueprint", "blueprint") for session_id in session_ids]

        assert format_reconciliation_warning(records) == expected


class ReconcileStaleRunsTests:
    def test_dead_pid_row_is_marked_failed_with_stale_message(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] reconcile_stale_sessions: dead-pid RUNNING row becomes FAILED with error_message == STALE_RUN_ERROR_MESSAGE; result.reconciled == [that row] and result.warning == 'Reconciled 1 interrupted session (session_id: <id>).'."""
        _kill_reports_no_such_process(monkeypatch)
        sessions = _sessions_for(isolated_workspace)
        sessions.create("session-dead", "blueprint", "blueprint", pid=DEAD_PID)

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-dead")
        assert stored is not None
        assert stored.status == SessionStatus.FAILED
        assert stored.error_message == STALE_RUN_ERROR_MESSAGE
        assert result.reconciled == [stored]
        assert result.warning == "Reconciled 1 interrupted session (session_id: session-dead)."

    def test_live_run_is_left_running(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] reconcile_stale_sessions: RUNNING row owned by os.getpid() stays RUNNING; result == ReconciliationResult(reconciled=[], warning=None)."""
        sessions = _sessions_for(isolated_workspace)
        sessions.create("session-live", "blueprint", "blueprint", pid=os.getpid())

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-live")
        assert stored is not None
        assert stored.status == SessionStatus.RUNNING
        assert result == ReconciliationResult(reconciled=[], warning=None)

    def test_repository_failure_returns_empty_result(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] reconcile_stale_sessions: SessionsRepository.list raising returns ReconciliationResult(reconciled=[], warning=None) and does not raise."""
        sessions = _sessions_for(isolated_workspace)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(SessionsRepository, "list", _boom)

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        assert result == ReconciliationResult(reconciled=[], warning=None)

    def test_detached_repository_without_path_returns_empty_result(self) -> None:
        """[tier-1/unit] reconcile_stale_sessions: path=None with a repository whose path is None returns ReconciliationResult(reconciled=[], warning=None)."""
        detached = SessionsRepository(path=None, auto_init=False)

        assert reconcile_stale_sessions(detached) == ReconciliationResult(reconciled=[], warning=None)
