"""Tier 1 domain tests for stale-run reconciliation."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
import pytest

from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.sessions import Reconciliation
from dovo.core.sessions.services.reconcile import (
    STALE_RUN_ERROR_MESSAGE,
    current_process_started_at,
    format_reconciliation_warning,
    get_process_start_time,
    is_pid_alive,
    is_run_stale,
    reconcile_stale_sessions,
)
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.lock import raise_lock_timeout
from tests.harness.sessions import seed_crashed_session
from tests.harness.workspace_paths import initialized_workspace_paths

DEAD_PID = 4242
ONE_PAUSED = "Paused 1 interrupted session; continue it with 'dovo resume <session_id>'."


def _raise_db_unavailable(*_args: object, **_kwargs: object) -> None:
    """Stand in for a SessionsRepository method whose database is unavailable."""
    raise RuntimeError("db unavailable")


def _sessions_for(root: Path) -> SessionsRepository:
    """Build the SessionsRepository for the workspace rooted at root."""
    paths = initialized_workspace_paths(root)
    return SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)


def _seed_crashed(
    root: Path,
    session_id: str,
    *,
    use_worktree: bool = False,
    worktree_id: str | None = None,
    worktree_path: Path | None = None,
) -> SessionsRepository:
    """Seed a crashed one-step run session_id in the workspace at root and return its SessionsRepository."""
    paths = initialized_workspace_paths(root)
    sessions = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
    seed_crashed_session(
        paths,
        sessions,
        session_id=session_id,
        steps=[{"id": "a", "type": "command", "command": "true"}],
        interrupted_step_id="a",
        use_worktree=use_worktree,
        worktree_id=worktree_id,
        worktree_path=worktree_path,
    )
    return sessions


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


def _child_started_at(child: subprocess.Popen[bytes], offset_seconds: float) -> str:
    """Format the child's OS creation time shifted by offset_seconds as a pid_started_at value."""
    child_start = get_process_start_time(child.pid)
    assert child_start is not None
    return (child_start + timedelta(seconds=offset_seconds)).isoformat(timespec="microseconds")


def _create_time_returns(monkeypatch: pytest.MonkeyPatch, create_time: float | Exception) -> None:
    """Make psutil.Process.create_time return create_time, or raise it when it is an exception."""

    def _create_time(_process: psutil.Process) -> float:
        if isinstance(create_time, Exception):
            raise create_time
        return create_time

    monkeypatch.setattr(psutil.Process, "create_time", _create_time)


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
        assert get_process_start_time(0) is None

    def test_live_child_start_time_is_utc_between_test_process_start_and_now(
        self, child_process: subprocess.Popen[bytes]
    ) -> None:
        test_process_start = get_process_start_time(os.getpid())

        child_start = get_process_start_time(child_process.pid)

        assert test_process_start is not None
        assert child_start is not None
        assert child_start.tzinfo == UTC
        assert test_process_start <= child_start <= datetime.now(UTC)

    def test_reaped_child_pid_returns_none(self, child_process: subprocess.Popen[bytes]) -> None:
        child_process.terminate()
        child_process.wait(timeout=10)

        assert get_process_start_time(child_process.pid) is None


class CurrentProcessStartedAtTests:
    @pytest.mark.parametrize(
        ("create_time", "expected"),
        [
            pytest.param(1792296249.25, "2026-10-18T04:04:09.250000+00:00", id="readable"),
            pytest.param(psutil.AccessDenied(), None, id="unreadable"),
        ],
    )
    def test_returns_iso_utc_microseconds_or_none(
        self, monkeypatch: pytest.MonkeyPatch, create_time: float | Exception, expected: str | None
    ) -> None:
        _create_time_returns(monkeypatch, create_time)

        assert current_process_started_at() == expected


class IsRunStaleTests:
    @pytest.mark.parametrize("status", [SessionStatus.COMPLETED, SessionStatus.FAILED])
    def test_non_running_status_is_not_stale(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, status: SessionStatus
    ) -> None:
        _kill_reports_no_such_process(monkeypatch)
        run = _sessions_for(isolated_workspace).create(
            "session-1", "blueprint", "blueprint", status=status, pid=DEAD_PID
        )

        assert is_run_stale(run) is False

    def test_running_row_without_pid_is_stale(self, isolated_workspace: Path) -> None:
        run = _sessions_for(isolated_workspace).create("session-1", "blueprint", "blueprint", pid=None)

        assert is_run_stale(run) is True

    @pytest.mark.parametrize(
        ("offset_seconds", "expected"),
        [
            pytest.param(0.0, False, id="within-same"),
            pytest.param(0.5, False, id="within-later"),
            pytest.param(-0.5, False, id="within-earlier"),
            pytest.param(5.0, True, id="beyond-later"),
            pytest.param(-5.0, True, id="beyond-earlier"),
        ],
    )
    def test_live_pid_staleness_follows_pid_started_at_not_started_at(
        self,
        isolated_workspace: Path,
        child_process: subprocess.Popen[bytes],
        offset_seconds: float,
        expected: bool,
    ) -> None:
        run = _sessions_for(isolated_workspace).create(
            "session-1",
            "blueprint",
            "blueprint",
            pid=child_process.pid,
            pid_started_at=_child_started_at(child_process, offset_seconds),
        )
        run = run.model_copy(update={"started_at": "2000-01-01 00:00:00"})

        assert is_run_stale(run) is expected

    @pytest.mark.parametrize(
        "pid_started_at", [pytest.param(None, id="null"), pytest.param("not-a-timestamp", id="unparseable")]
    )
    def test_live_pid_without_comparable_pid_started_at_is_not_stale(
        self, isolated_workspace: Path, child_process: subprocess.Popen[bytes], pid_started_at: str | None
    ) -> None:
        run = _sessions_for(isolated_workspace).create(
            "session-1", "blueprint", "blueprint", pid=child_process.pid, pid_started_at=pid_started_at
        )

        assert is_run_stale(run) is False

    def test_live_pid_with_unreadable_creation_time_is_not_stale(
        self, isolated_workspace: Path, child_process: subprocess.Popen[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _sessions_for(isolated_workspace).create(
            "session-1",
            "blueprint",
            "blueprint",
            pid=child_process.pid,
            pid_started_at="2000-01-01T00:00:00.000000+00:00",
        )
        _create_time_returns(monkeypatch, psutil.AccessDenied())

        assert is_run_stale(run) is False


class FormatReconciliationWarningTests:
    @pytest.mark.parametrize(
        ("paused_ids", "failed_ids", "expected"),
        [
            pytest.param([], [], None, id="none"),
            pytest.param(["a"], [], ONE_PAUSED, id="one-paused"),
            pytest.param(
                ["a", "b"],
                [],
                "Paused 2 interrupted sessions (a, b); continue each with 'dovo resume <session_id>'.",
                id="two-paused",
            ),
            pytest.param([], ["a"], "Marked 1 interrupted session failed (session_id: a).", id="one-failed"),
            pytest.param([], ["a", "b"], "Marked 2 interrupted sessions failed (a, b).", id="two-failed"),
            pytest.param(
                ["a"],
                ["b"],
                f"{ONE_PAUSED} Marked 1 interrupted session failed (session_id: b).",
                id="paused-and-failed",
            ),
        ],
    )
    def test_sentences_name_their_sessions(
        self, isolated_workspace: Path, paused_ids: list[str], failed_ids: list[str], expected: str | None
    ) -> None:
        sessions = _sessions_for(isolated_workspace)
        paused = [sessions.create(session_id, "blueprint", "blueprint") for session_id in paused_ids]
        failed = [sessions.create(session_id, "blueprint", "blueprint") for session_id in failed_ids]

        assert format_reconciliation_warning(paused, failed) == expected


class ReconcileStaleRunsTests:
    @pytest.mark.parametrize("pid_started_at", [None, "2026-10-18T04:04:09.250000+00:00"])
    def test_dead_pid_row_is_marked_failed_with_stale_message(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, pid_started_at: str | None
    ) -> None:
        _kill_reports_no_such_process(monkeypatch)
        sessions = _sessions_for(isolated_workspace)
        sessions.create("session-dead", "blueprint", "blueprint", pid=DEAD_PID, pid_started_at=pid_started_at)

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-dead")
        assert stored is not None
        assert stored.status == SessionStatus.FAILED
        assert stored.error_message == STALE_RUN_ERROR_MESSAGE
        assert result.failed == (stored,)
        assert result.paused == ()
        assert result.warning == "Marked 1 interrupted session failed (session_id: session-dead)."

    @pytest.mark.parametrize(
        ("use_worktree", "worktree_id", "worktree_dir", "expected_status", "expected_error"),
        [
            pytest.param(False, None, None, SessionStatus.PAUSED, None, id="no-worktree"),
            pytest.param(True, "wt-1", "present", SessionStatus.PAUSED, None, id="worktree-present"),
            pytest.param(
                True, "wt-1", "missing", SessionStatus.FAILED, STALE_RUN_ERROR_MESSAGE, id="worktree-dir-missing"
            ),
            pytest.param(True, None, None, SessionStatus.FAILED, STALE_RUN_ERROR_MESSAGE, id="no-worktree-id"),
            pytest.param(True, "wt-1", None, SessionStatus.FAILED, STALE_RUN_ERROR_MESSAGE, id="no-worktree-row"),
        ],
    )
    def test_crashed_run_is_paused_only_when_resumable(
        self,
        isolated_workspace: Path,
        use_worktree: bool,
        worktree_id: str | None,
        worktree_dir: str | None,
        expected_status: SessionStatus,
        expected_error: str | None,
    ) -> None:
        worktree_path = None
        if worktree_dir is not None:
            worktree_path = isolated_workspace / ".dovo" / "worktrees" / "wt-1"
            if worktree_dir == "present":
                worktree_path.mkdir(parents=True)
        sessions = _seed_crashed(
            isolated_workspace,
            "session-crashed",
            use_worktree=use_worktree,
            worktree_id=worktree_id,
            worktree_path=worktree_path,
        )

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-crashed")
        assert stored is not None
        assert stored.status == expected_status
        assert stored.error_message == expected_error
        if expected_status == SessionStatus.PAUSED:
            assert result.warning == ONE_PAUSED
        else:
            assert result.warning == "Marked 1 interrupted session failed (session_id: session-crashed)."

    def test_recorded_worktree_outside_the_reconciling_root_is_paused(
        self, isolated_workspace: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        worktree_path = tmp_path_factory.mktemp("elsewhere") / "wt-1"
        worktree_path.mkdir()
        sessions = _seed_crashed(
            isolated_workspace, "session-elsewhere", use_worktree=True, worktree_id="wt-1", worktree_path=worktree_path
        )

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-elsewhere")
        assert stored is not None
        assert stored.status == SessionStatus.PAUSED
        assert result.paused == (stored,)

    @pytest.mark.parametrize(
        ("session_id", "child_session_id", "expected_status"),
        [
            pytest.param("crashed-same", "crashed-same", SessionStatus.FAILED, id="same-id"),
            pytest.param("crashed-other", "other", SessionStatus.PAUSED, id="other-id"),
        ],
    )
    def test_live_process_carrying_the_session_id_fails_the_run(
        self, isolated_workspace: Path, session_id: str, child_session_id: str, expected_status: SessionStatus
    ) -> None:
        """Each case has its own run id, so a child left running by the other case never matches it."""
        sessions = _seed_crashed(isolated_workspace, session_id)
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env={**os.environ, "DOVO_SESSION_ID": child_session_id},
        )
        try:
            reconcile_stale_sessions(sessions, path=isolated_workspace)
        finally:
            child.kill()
            child.wait()

        stored = sessions.get(session_id)
        assert stored is not None
        assert stored.status == expected_status

    def test_unreadable_process_environment_is_not_counted(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sessions = _seed_crashed(isolated_workspace, "session-unreadable")

        def _environ(_process: psutil.Process) -> dict[str, str]:
            raise psutil.AccessDenied()

        monkeypatch.setattr(psutil.Process, "environ", _environ)

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-unreadable")
        assert stored is not None
        assert stored.status == SessionStatus.PAUSED
        assert result.diagnostic is None

    def test_paused_and_failed_rows_are_reported_together(self, isolated_workspace: Path) -> None:
        sessions = _seed_crashed(isolated_workspace, "session-resumable")
        sessions.create("session-stateless", "blueprint", "blueprint")

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        paused = sessions.get("session-resumable")
        failed = sessions.get("session-stateless")
        assert paused is not None
        assert failed is not None
        assert result.paused == (paused,)
        assert result.failed == (failed,)
        assert result.warning == f"{ONE_PAUSED} Marked 1 interrupted session failed (session_id: session-stateless)."

    @pytest.mark.parametrize(
        ("offset_seconds", "expected_status", "expected_error"),
        [
            pytest.param(0.0, SessionStatus.RUNNING, None, id="same-process"),
            pytest.param(-3600.0, SessionStatus.FAILED, STALE_RUN_ERROR_MESSAGE, id="reused-pid"),
        ],
    )
    def test_live_pid_row_is_reconciled_by_recorded_creation_time(
        self,
        isolated_workspace: Path,
        child_process: subprocess.Popen[bytes],
        offset_seconds: float,
        expected_status: SessionStatus,
        expected_error: str | None,
    ) -> None:
        sessions = _sessions_for(isolated_workspace)
        sessions.create(
            "session-live",
            "blueprint",
            "blueprint",
            pid=child_process.pid,
            pid_started_at=_child_started_at(child_process, offset_seconds),
        )

        reconcile_stale_sessions(sessions, path=isolated_workspace)

        stored = sessions.get("session-live")
        assert stored is not None
        assert stored.status == expected_status
        assert stored.error_message == expected_error

    def test_repository_failure_records_error_in_diagnostic(
        self, isolated_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return Reconciliation(diagnostic=diagnostic_record(frozen_diagnostic_clock)) and write one sessions.reconcile error event with context {"path": <workspace>}."""
        sessions = _sessions_for(isolated_workspace)
        monkeypatch.setattr(SessionsRepository, "list", _raise_db_unavailable)

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        assert result == Reconciliation(diagnostic=diagnostic_record(frozen_diagnostic_clock))
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "sessions.reconcile"
        assert event["context"] == {"path": str(isolated_workspace)}
        assert event["exception_message"] == "db unavailable"

    def test_lock_timeout_propagates_without_record(
        self, isolated_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With raise_lock_timeout, raise LockTimeoutError("lock held") and leave <DOVO_HOME>/logs absent."""
        sessions = _sessions_for(isolated_workspace)
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        with pytest.raises(LockTimeoutError, match="lock held"):
            reconcile_stale_sessions(sessions, path=isolated_workspace)

        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_detached_repository_without_path_returns_empty_reconciliation(self, frozen_diagnostic_clock: Path) -> None:
        """Return Reconciliation() and leave <DOVO_HOME>/logs absent for a detached repository without a path."""
        detached = SessionsRepository(path=None, auto_init=False)

        assert reconcile_stale_sessions(detached) == Reconciliation()
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_repository_failure_unchanged_when_recorder_cannot_write(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return Reconciliation() when <DOVO_HOME>/logs is a regular file."""
        sessions = _sessions_for(isolated_workspace)
        monkeypatch.setattr(SessionsRepository, "list", _raise_db_unavailable)
        logs_dir = resolve_global_paths().logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        result = reconcile_stale_sessions(sessions, path=isolated_workspace)

        assert result == Reconciliation()
