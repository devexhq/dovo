"""Tests for the database probe through Status.collect()."""

from __future__ import annotations

from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.status import Status
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_paths


class DatabaseProbeTests:
    """Tests for database status collection."""

    def test_collect_missing_database_reports_inaccessible(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(database=False)

        result = Status(initialized_workspace_paths(workspace)).collect()

        assert result.database.exists is False
        assert result.database.is_accessible is False

    def test_collect_corrupted_database_reports_inaccessible(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace()

        (resolve_global_paths(None).data_dir / "dovo.db").write_bytes(b"NOT A SQLITE DATABASE")

        result = Status(initialized_workspace_paths(workspace)).collect()

        assert result.database.exists is True
        assert result.database.is_accessible is False
        assert result.database.total_sessions == 0
        assert result.worktrees.active_worktrees == 0
        assert result.worktrees.total_worktrees == 0
        assert result.warnings == []
