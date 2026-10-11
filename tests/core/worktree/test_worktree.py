"""Contract tests for the Worktree entrypoint: collaborator wiring and pass-through delegation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository
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


class WorktreeConstructionTests:
    def test_default_db_is_bound_to_the_given_paths(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] Worktree.__init__: omitting db constructs a WorktreesRepository scoped to the given paths, distinct per instance."""
        worktree = Worktree(worktree_workspace_paths)
        assert worktree.db.db_path == worktree_workspace_paths.database_file
        assert worktree.db.project_id == worktree_workspace_paths.project_id

    def test_lifecycle_and_patch_share_the_same_path_and_db(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] Worktree.__init__: the constructed lifecycle and patch collaborators are bound to the same path and db as the entrypoint."""
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        worktree = Worktree(worktree_workspace_paths, db=db)

        assert worktree.lifecycle.path == worktree.path
        assert worktree.lifecycle.db is db
        assert worktree.patch.db is db
        assert worktree.patch.lifecycle is worktree.lifecycle

    def test_worktree_base_dir_delegates_to_lifecycle(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] Worktree.worktree_base_dir: the property returns exactly what the underlying lifecycle returns."""
        worktree = Worktree(worktree_workspace_paths)
        assert worktree.worktree_base_dir == worktree.lifecycle.worktree_base_dir


class WorktreeCreateListShowDeleteDelegationTests:
    def test_create_persists_a_worktree_that_list_and_show_can_find(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] Worktree.create/.list/.show: a worktree created through the entrypoint is visible via list() and show() with matching id."""
        worktree = Worktree(worktree_workspace_paths)

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
        """[tier-1/integration] Worktree.create/.delete: delete() returns a READY status referencing the worktree created through the entrypoint."""
        worktree = Worktree(worktree_workspace_paths)
        worktree.create(session_id="dovo_entrypoint_delete")

        delete_result = worktree.delete("dovo_entrypoint_delete")

        assert delete_result.worktree_id == "dovo_entrypoint_delete"
        assert delete_result.worktree is not None


class WorktreeCleanupPruneGetActiveDelegationTests:
    def test_get_active_lists_the_directory_created_by_create(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Worktree.create/.get_active: the worktree directory created by create() appears in get_active()."""
        worktree = Worktree(worktree_workspace_paths)
        create_result = worktree.create(session_id="dovo_entrypoint_active")
        assert create_result.session is not None

        active = worktree.get_active()

        assert create_result.session.worktree_path in active

    def test_cleanup_removes_the_worktree_dovo_directory(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Worktree.create/.cleanup: cleanup() removes the worktree directory created by create()."""
        worktree = Worktree(worktree_workspace_paths)
        create_result = worktree.create(session_id="dovo_entrypoint_cleanup")
        assert create_result.session is not None

        warnings = worktree.cleanup(create_result.session)

        assert warnings == []
        assert not create_result.session.worktree_path.exists()

    def test_prune_reports_no_stale_worktrees_for_a_freshly_created_one(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] Worktree.create/.prune: prune() on a workspace with one freshly created, still-active worktree reports it as not pruned."""
        worktree = Worktree(worktree_workspace_paths)
        worktree.create(session_id="dovo_entrypoint_prune")

        result = worktree.prune(dry_run=True)

        assert all(item.identifier != "dovo_entrypoint_prune" for item in result.items)


class WorktreeDiffApplyDelegationTests:
    def test_diff_on_freshly_created_worktree_reports_empty_diff(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] Worktree.create/.diff: a freshly created worktree with no further commits reports EMPTY_DIFF through the entrypoint's patch delegation."""
        worktree = Worktree(worktree_workspace_paths)
        create_result = worktree.create(session_id="dovo_entrypoint_diff")
        assert create_result.status == WorktreeCreateStatus.OK

        diff_result = worktree.diff("dovo_entrypoint_diff")

        assert diff_result.status == WorktreeDiffStatus.EMPTY_DIFF

    def test_apply_on_unknown_worktree_id_returns_not_found(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Worktree.apply: an unknown worktree id delegates through to patch.apply and returns NOT_FOUND."""
        worktree = Worktree(worktree_workspace_paths)

        result = worktree.apply("missing_worktree")

        assert result.status == WorktreeApplyStatus.NOT_FOUND
