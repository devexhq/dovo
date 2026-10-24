"""Contract tests for the Worktree entrypoint's pass-through delegation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    WorktreeApplyStatus,
    WorktreeCreateStatus,
    WorktreeDiffStatus,
    WorktreeListStatus,
    WorktreeShowStatus,
)
from dovo.core.worktree.worktree import Worktree
from tests.harness import WorkspaceBuilder


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a worktree database."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


@pytest.fixture
def worktree_workspace_paths(
    worktree_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's worktree workspace."""
    return workspace_paths_factory(worktree_workspace, None)


class WorktreeCreateListShowDeleteDelegationTests:
    def test_create_persists_a_worktree_that_list_and_show_can_find(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner())

        create_result = worktree.create(session_id="dovo_entrypoint_create")
        assert create_result.status == WorktreeCreateStatus.OK

        list_result = worktree.list()
        assert list_result.status == WorktreeListStatus.OK
        assert any(row.id == "dovo_entrypoint_create" for row in list_result.worktrees)

        show_result = worktree.show("dovo_entrypoint_create")
        assert show_result.status == WorktreeShowStatus.OK
        assert show_result.worktree is not None
        assert show_result.worktree.id == "dovo_entrypoint_create"

    def test_delete_reflects_the_same_worktree_created_through_the_entrypoint(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner())
        worktree.create(session_id="dovo_entrypoint_delete")

        delete_result = worktree.delete("dovo_entrypoint_delete")

        assert delete_result.worktree_id == "dovo_entrypoint_delete"
        assert delete_result.worktree is not None


class WorktreeCleanupDelegationTests:
    def test_cleanup_removes_the_worktree_dovo_directory(self, worktree_workspace_paths: WorkspacePaths) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner())
        create_result = worktree.create(session_id="dovo_entrypoint_cleanup")
        assert create_result.session is not None

        warnings = worktree.cleanup(create_result.session)

        assert warnings == []
        assert not create_result.session.worktree_path.exists()


class WorktreeDiffApplyDelegationTests:
    def test_diff_on_freshly_created_worktree_reports_empty_diff(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner())
        create_result = worktree.create(session_id="dovo_entrypoint_diff")
        assert create_result.status == WorktreeCreateStatus.OK

        diff_result = worktree.diff("dovo_entrypoint_diff")

        assert diff_result.status == WorktreeDiffStatus.EMPTY_DIFF

    def test_apply_on_unknown_worktree_id_returns_not_found(self, worktree_workspace_paths: WorkspacePaths) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner())

        result = worktree.apply("missing_worktree")

        assert result.status == WorktreeApplyStatus.NOT_FOUND
