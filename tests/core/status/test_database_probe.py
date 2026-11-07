"""Tests for the database probe through Status.collect()."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import SessionsRepository
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status, WorkspaceHealth
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import RecordingStepStateCloser
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


class DatabaseProbeTests:
    """Tests for database status collection."""

    def test_collect_missing_database_reports_inaccessible(
        self, status_workspace: BuildStatusWorkspace, tmp_path: Path
    ) -> None:
        """With database=False and sessions_db on tmp_path / "reconcile.db", report database.exists and is_accessible False."""
        workspace = status_workspace(database=False)
        paths = initialized_workspace_paths(workspace)
        sessions_db = SessionsRepository(db_path=tmp_path / "reconcile.db", project_id=paths.project_id)

        result = Status(paths, git=GitRunner(), sessions_db=sessions_db, closer=RecordingStepStateCloser()).collect()

        assert result.database.exists is False
        assert result.database.is_accessible is False

    def test_collect_corrupted_database_reports_inaccessible(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace()

        (resolve_global_paths(None).data_dir / "dovo.db").write_bytes(b"NOT A SQLITE DATABASE")

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
            closer=RecordingStepStateCloser(),
        ).collect()

        assert result.database.exists is True
        assert result.database.is_accessible is False
        assert result.database.total_sessions == 0
        assert result.worktrees.active_worktrees == 0
        assert result.worktrees.total_worktrees == 0
        assert result.warnings == []

    def test_collect_unreadable_database_records_the_probe_failure(
        self, status_workspace: BuildStatusWorkspace, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """With a corrupted project database and a healthy reconcile.db, list the database probe's own record in diagnostics."""
        workspace = status_workspace()
        paths = initialized_workspace_paths(workspace)
        sessions_db = SessionsRepository(db_path=tmp_path / "reconcile.db", project_id=paths.project_id)
        paths.database_file.write_bytes(b"NOT A SQLITE DATABASE")

        result = Status(paths, git=GitRunner(), sessions_db=sessions_db, closer=RecordingStepStateCloser()).collect()

        record = diagnostic_record(frozen_diagnostic_clock)
        assert result.status is WorkspaceHealth.DEGRADED
        assert result.database.diagnostic == record
        assert result.diagnostics == [record]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "status.database"
