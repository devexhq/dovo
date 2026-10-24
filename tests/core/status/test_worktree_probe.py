"""Tests for the worktree probe through Status.collect()."""

from __future__ import annotations

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.db import SessionsRepository, SessionStatus, WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status
from tests.harness.status_workspace import BuildStatusWorkspace, status_config_payload
from tests.harness.workspace_paths import initialized_workspace_paths


class WorktreeProbeTests:
    """Tests for worktree inventory collection."""

    def test_collect_without_database_counts_worktree_directories(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(
            database=False, config=status_config_payload(model="gpt-4o", max_active_worktrees=4)
        )
        fs = Filesystem(workspace)

        (fs.worktrees_dir / "sb-1").mkdir(parents=True, exist_ok=True)
        (fs.worktrees_dir / "sb-2").mkdir(parents=True, exist_ok=True)

        result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()

        assert result.database.exists is False
        assert result.worktrees.active_worktrees == 2
        assert result.worktrees.total_worktrees == 2
        assert result.worktrees.max_active_worktrees == 4

    def test_collect_worktree_query_error_counts_worktree_directories(
        self,
        status_workspace: BuildStatusWorkspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = status_workspace()
        fs = Filesystem(workspace)
        paths = initialized_workspace_paths(workspace)

        sessions_repo = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        sessions_repo.create(
            session_id="sess-test",
            blueprint_name="test-bp",
            blueprint_key="test-bp",
            status=SessionStatus.COMPLETED,
        )
        (fs.worktrees_dir / "sb-fallback").mkdir(parents=True, exist_ok=True)

        def mock_list(*args: object, **kwargs: object) -> list[object]:
            raise RuntimeError("DB query failure")

        monkeypatch.setattr(WorktreesRepository, "list", mock_list)

        result = Status(paths, git=GitRunner()).collect()

        assert result.database.total_sessions == 1
        assert result.worktrees.active_worktrees == 1
        assert result.worktrees.total_worktrees == 1
