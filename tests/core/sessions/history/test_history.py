"""Tier 1 domain tests for History entrypoint coordinator."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import RunRecord, RunsRepository, RunStatus
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.sessions.history import History
from dovo.core.sessions.history.models import (
    HistoryListStatus,
    HistoryShowStatus,
    ReconciliationResult,
)
from dovo.core.sessions.logs import RunLogEvent, RunLogEventType


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _as_expected_record(record: RunRecord) -> RunRecord:
    """Construct an explicit expected RunRecord instance with all model fields set."""
    return RunRecord.model_construct(
        id=record.id,
        project_id=record.project_id,
        session_id=record.session_id,
        blueprint_key=record.blueprint_key,
        blueprint_name=record.blueprint_name,
        branch_name=record.branch_name,
        status=record.status,
        pid=record.pid,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error_message=record.error_message,
    )


class HistoryInitializationTests:
    """Unit tests for History class constructor and dependency defaults."""

    def test_default_initialization_constructs_runs_repository_from_resolved_path(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/unit] History.__init__: omitting db argument initializes RunsRepository with resolved path."""
        paths = _paths_for(isolated_workspace)
        history = History(paths)

        assert history.path == isolated_workspace.resolve()
        assert isinstance(history.db, RunsRepository)
        assert history.db.db_path == paths.database_file


class HistoryListTests:
    """Integration tests for History.list querying and reconciliation."""

    def test_empty_database_returns_ok_status_with_empty_runs(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.list: empty runs table returns HistoryListResult with status=OK, runs=[], warnings=[]."""
        history = History(_paths_for(isolated_workspace))

        result = history.list()

        assert result.status == HistoryListStatus.OK
        assert result.runs == []
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is True

    def test_unfiltered_list_returns_runs_ordered_with_ok_status(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.list: returns all runs up to default limit with status=OK in descending order."""
        history = History(_paths_for(isolated_workspace))
        run_first = history.db.create(
            session_id="session-001",
            blueprint_name="task-alpha",
            blueprint_key="task-alpha",
            status=RunStatus.COMPLETED,
        )
        run_second = history.db.create(
            session_id="session-002",
            blueprint_name="task-beta",
            blueprint_key="task-beta",
            status=RunStatus.FAILED,
        )

        result = history.list()

        assert result.status == HistoryListStatus.OK
        assert result.runs == [_as_expected_record(run_second), _as_expected_record(run_first)]
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is True

    def test_limit_parameter_restricts_number_of_returned_runs(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.list: limit=N restricts returned runs to N records."""
        history = History(_paths_for(isolated_workspace))
        history.db.create(
            session_id="session-001",
            blueprint_name="task-alpha",
            blueprint_key="task-alpha",
            status=RunStatus.COMPLETED,
        )
        history.db.create(
            session_id="session-002",
            blueprint_name="task-beta",
            blueprint_key="task-beta",
            status=RunStatus.COMPLETED,
        )
        run_third = history.db.create(
            session_id="session-003",
            blueprint_name="task-gamma",
            blueprint_key="task-gamma",
            status=RunStatus.COMPLETED,
        )

        result = history.list(limit=1)

        assert result.status == HistoryListStatus.OK
        assert result.runs == [_as_expected_record(run_third)]
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is True

    @pytest.mark.parametrize(
        ("status_arg", "expected_session_id"),
        [
            pytest.param("completed", "session-comp", id="lowercase-completed"),
            pytest.param("COMPLETED", "session-comp", id="uppercase-completed"),
            pytest.param("failed", "session-fail", id="lowercase-failed"),
            pytest.param("FAILED", "session-fail", id="uppercase-failed"),
            pytest.param("running", "session-run", id="lowercase-running"),
        ],
    )
    def test_status_filter_filters_matching_runs(
        self, isolated_workspace: Path, status_arg: str, expected_session_id: str
    ) -> None:
        """[tier-1/integration] History.list: status filter correctly filters runs by status enum and case-insensitively."""
        history = History(_paths_for(isolated_workspace))
        run_comp = history.db.create(
            session_id="session-comp",
            blueprint_name="task-comp",
            blueprint_key="task-comp",
            status=RunStatus.COMPLETED,
        )
        run_fail = history.db.create(
            session_id="session-fail",
            blueprint_name="task-fail",
            blueprint_key="task-fail",
            status=RunStatus.FAILED,
        )
        # Using current process PID ensures is_run_stale is False so reconcile_stale_runs does not flip status to FAILED.
        run_active = history.db.create(
            session_id="session-run",
            blueprint_name="task-run",
            blueprint_key="task-run",
            status=RunStatus.RUNNING,
            pid=os.getpid(),
        )
        lookup: dict[str, RunRecord] = {
            "session-comp": run_comp,
            "session-fail": run_fail,
            "session-run": run_active,
        }

        result = history.list(status=status_arg)

        assert result.status == HistoryListStatus.OK
        assert result.runs == [_as_expected_record(lookup[expected_session_id])]
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is True

    def test_unknown_status_filter_fallback_passes_raw_string_to_query(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.list: invalid status string falls back to raw string filter without raising ValueError."""
        history = History(_paths_for(isolated_workspace))
        history.db.create(
            session_id="session-comp",
            blueprint_name="task-comp",
            blueprint_key="task-comp",
            status=RunStatus.COMPLETED,
        )

        result = history.list(status="nonexistent_status_value")

        assert result.status == HistoryListStatus.OK
        assert result.runs == []
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is True

    def test_stale_run_reconciliation_warning_is_captured_in_result(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] History.list: reconciliation warning from reconcile_stale_runs is appended to warnings."""
        history = History(_paths_for(isolated_workspace))

        def _mock_reconcile(*_args: object, **_kwargs: object) -> ReconciliationResult:
            return ReconciliationResult(reconciled=[], warning="Session was terminated abnormally")

        monkeypatch.setattr("dovo.core.sessions.history.history.reconcile_stale_runs", _mock_reconcile)

        result = history.list()

        assert result.status == HistoryListStatus.OK
        assert result.runs == []
        assert result.errors == []
        assert result.warnings == ["Session was terminated abnormally"]
        assert result.fixes == []
        assert result.ok is True


class HistoryShowTests:
    """Integration tests for History.show session lookup."""

    def test_missing_session_id_returns_not_found_status(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.show: missing session_id returns HistoryShowResult with status=NOT_FOUND and run=None."""
        history = History(_paths_for(isolated_workspace))

        result = history.show("nonexistent-session")

        assert result.status == HistoryShowStatus.NOT_FOUND
        assert result.session_id == "nonexistent-session"
        assert result.run is None
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is False

    def test_existing_session_id_returns_ok_status_with_run_record(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.show: existing session_id returns HistoryShowResult with status=OK and run record."""
        history = History(_paths_for(isolated_workspace))
        seeded_run = history.db.create(
            session_id="session-alpha",
            blueprint_name="deploy-flow",
            blueprint_key="deploy-flow",
            status=RunStatus.COMPLETED,
        )

        result = history.show("session-alpha")

        assert result.status == HistoryShowStatus.OK
        assert result.session_id == "session-alpha"
        assert result.run == _as_expected_record(seeded_run)
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.ok is True


class HistoryShowIncludeLogsTests:
    """[tier-1/integration] History.show(include_logs=...): log file listing and run.log snippet."""

    def _seed_logged_session(self, history: History, workspace: Path) -> Path:
        """Persist a run record and a log directory with twelve run.log events and one stdout capture."""
        history.db.create(session_id="sess", blueprint_name="bp", blueprint_key="bp", status=RunStatus.COMPLETED)
        session_log_dir = (
            resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None)).logs_dir / "sess"
        )
        session_log_dir.mkdir(parents=True)
        events = [
            RunLogEvent(ts=f"2026-09-26T10:00:{i:02d}+00:00", event=RunLogEventType.STEP_START, step_id=f"s{i}")
            for i in range(1, 13)
        ]
        (session_log_dir / "run.log").write_text("".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8")
        (session_log_dir / "01_s1_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")
        return session_log_dir

    def test_show_include_logs_true_populates_log_files_and_snippet(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.show: include_logs=True lists every file under logs_dir/<session_id>/ and renders the last 10 run.log events."""
        history = History(_paths_for(isolated_workspace))
        session_log_dir = self._seed_logged_session(history, isolated_workspace)

        result = history.show("sess", include_logs=True)

        assert result.log_files == [
            str(session_log_dir / "01_s1_attempt_1.stdout.log"),
            str(session_log_dir / "run.log"),
        ]
        assert result.log_snippet == [f"[2026-09-26T10:00:{i:02d}+00:00] step_start step_id=s{i}" for i in range(3, 13)]

    def test_show_include_logs_false_default_leaves_log_fields_empty(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.show: the default call leaves log_files and log_snippet empty even when logs exist."""
        history = History(_paths_for(isolated_workspace))
        self._seed_logged_session(history, isolated_workspace)

        result = history.show("sess")

        assert (result.log_files, result.log_snippet) == ([], [])

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
    def test_show_include_logs_unreadable_run_log_returns_error_result(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] History.show: an OSError reading run.log is returned in errors with empty log fields."""
        history = History(_paths_for(isolated_workspace))
        session_log_dir = self._seed_logged_session(history, isolated_workspace)
        (session_log_dir / "run.log").chmod(0)

        result = history.show("sess", include_logs=True)

        assert result.ok is False
        assert (result.log_files, result.log_snippet) == ([], [])
        assert len(result.errors) == 1
        assert "Permission denied" in result.errors[0]
