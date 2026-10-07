from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    WorktreeCreateStatus,
)
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


@pytest.fixture
def worktree_workspace_paths(
    worktree_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's worktree workspace."""
    return workspace_paths_factory(worktree_workspace, None)


class WorktreeCreationTests:
    """Integration tests verifying worktree creation and metadata recording."""

    def test_create_initializes_worktree_and_branch_metadata(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
    ) -> None:
        """Create initializes worktree on disk, git branch, and DB row."""
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)

        result = lifecycle.create(session_id="dovo_test001", name="test-worktree")

        expected_worktree_path = (worktree_workspace / ".dovo" / "worktrees" / "dovo_test001").resolve()
        expected_branch = "dovo/dovo_test001"
        head_commit = GitRunner.rev_parse(worktree_workspace, rev="HEAD")

        assert result.status == WorktreeCreateStatus.OK
        assert result.session is not None
        assert result.session.session_id == "dovo_test001"
        assert result.session.target_branch == expected_branch
        assert result.session.worktree_path == expected_worktree_path
        assert result.session.base_commit == head_commit
        assert result.session.name == "test-worktree"
        assert result.session.created_at is not None
        assert result.session.command_passed is None
        assert result.session.wip_applied is False
        assert result.session.wip_paths == []
        assert result.warnings == []
        assert result.errors == []
        assert result.fixes == []
        assert expected_worktree_path.is_dir()
        assert expected_branch in GitRunner.list_branches(worktree_workspace)

        record = db.get("dovo_test001")
        assert record is not None
        assert record.id == "dovo_test001"
        assert record.name == "test-worktree"
        assert record.branch_name == expected_branch
        assert record.base_commit == head_commit
        assert record.worktree_path == expected_worktree_path
        assert record.status == WorktreeStatus.ACTIVE
        assert record.created_at is not None
        assert record.updated_at is not None


class WorktreeCapacityTests:
    """Integration tests verifying capacity ceiling enforcement."""

    def test_create_enforces_max_active_worktrees_limit(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
    ) -> None:
        """Creating a worktree beyond max_active_worktrees returns CAPACITY_EXCEEDED."""
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)

        assert DEFAULT_MAXIMUM_WORKTREES_ALLOWED >= 1
        for idx in range(DEFAULT_MAXIMUM_WORKTREES_ALLOWED):
            lifecycle.create(f"dovo_cap_{idx + 1}")

        overflow_id = f"dovo_cap_{DEFAULT_MAXIMUM_WORKTREES_ALLOWED + 1}"
        overflow = lifecycle.create(overflow_id)

        assert overflow.status == WorktreeCreateStatus.CAPACITY_EXCEEDED
        assert overflow.session is None
        assert overflow.errors == [
            f"Maximum active worktrees reached ({DEFAULT_MAXIMUM_WORKTREES_ALLOWED}/{DEFAULT_MAXIMUM_WORKTREES_ALLOWED})."
        ]
        assert overflow.warnings == []
        assert overflow.fixes == [
            "Run `dovo prune` to remove stale worktrees, or",
            "Raise worktree.max_active_worktrees in .dovo/config.json",
        ]
        assert not (worktree_workspace / ".dovo" / "worktrees" / overflow_id).exists()
        assert f"dovo/{overflow_id}" not in GitRunner.list_branches(worktree_workspace)
        assert db.get(overflow_id) is None
