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
    WorktreeDeleteStatus,
    WorktreeListStatus,
    WorktreeShowStatus,
)
from dovo.core.worktree.services.delete import collect_worktree_delete
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.list import collect_worktree_list
from dovo.core.worktree.services.show import collect_worktree_show
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


@pytest.fixture
def workspace_paths_no_identity(
    tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve a workspace snapshot without project identity for guard-path tests."""
    return workspace_paths_factory(tmp_path / "uninitialized", None)


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


class WorktreeNotInitializedGateTests:
    """[tier-1/unit] Defensive status gates avoid database and filesystem access."""

    def test_worktree_create_returns_not_initialized_without_touching_disk_or_db(
        self, workspace_paths_no_identity: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create: absent project identity returns NOT_INITIALIZED before repository creation."""

        def _unexpected_create(self: WorktreesRepository, *args: object, **kwargs: object) -> None:
            raise AssertionError("database must not be used")

        monkeypatch.setattr(WorktreesRepository, "create", _unexpected_create)
        db = WorktreesRepository(
            db_path=workspace_paths_no_identity.database_file, project_id=workspace_paths_no_identity.project_id
        )

        result = WorktreeLifecycle(workspace_paths_no_identity, db).create("dovo_no_identity")

        assert result.status == WorktreeCreateStatus.NOT_INITIALIZED
        assert not workspace_paths_no_identity.worktrees_dir.exists()

    def test_worktree_list_returns_not_initialized_without_querying_db(
        self, workspace_paths_no_identity: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list: absent project identity skips both reconciliation and row lookup."""

        def _unexpected_query(self: WorktreesRepository, *args: object, **kwargs: object) -> None:
            raise AssertionError("database must not be queried")

        monkeypatch.setattr(WorktreesRepository, "reconcile_stale_active", _unexpected_query)
        monkeypatch.setattr(WorktreesRepository, "list", _unexpected_query)
        db = WorktreesRepository(
            db_path=workspace_paths_no_identity.database_file, project_id=workspace_paths_no_identity.project_id
        )

        result = collect_worktree_list(workspace_paths_no_identity, db)

        assert result.status == WorktreeListStatus.NOT_INITIALIZED
        assert result.worktrees == []

    def test_worktree_show_and_delete_return_not_initialized_without_querying_db(
        self, workspace_paths_no_identity: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """show/delete: absent project identity skips the repository lookup for both status variants."""

        def _unexpected_get(self: WorktreesRepository, *args: object, **kwargs: object) -> None:
            raise AssertionError("database must not be queried")

        monkeypatch.setattr(WorktreesRepository, "get", _unexpected_get)
        db = WorktreesRepository(
            db_path=workspace_paths_no_identity.database_file, project_id=workspace_paths_no_identity.project_id
        )

        show_result = collect_worktree_show(workspace_paths_no_identity, db, "dovo_no_identity")
        delete_result = collect_worktree_delete(workspace_paths_no_identity, db, worktree_id="dovo_no_identity")

        assert show_result.status == WorktreeShowStatus.NOT_INITIALIZED
        assert delete_result.status == WorktreeDeleteStatus.NOT_INITIALIZED
