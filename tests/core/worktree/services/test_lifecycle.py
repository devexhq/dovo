"""Integration tests for worktree lifecycle behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeCreateStatus
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from tests.harness import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a worktree database."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


def _paths(workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for workspace, reflecting its current project.json."""
    return initialized_workspace_paths(workspace)


class WorktreeLifecycleStorageIndependenceTests:
    """[tier-1/integration] WorktreeLifecycle: worktree create and cleanup never manage session storage."""

    def test_create_makes_no_run_symlink_and_no_session_directory(self, worktree_workspace: Path) -> None:
        """[tier-1/integration] WorktreeLifecycle.create: session_id 'dovo_independent' returns OK, <worktree>/.dovo/run does not exist, and paths.session_dir('dovo_independent') does not exist."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_independent")

        assert result.status == WorktreeCreateStatus.OK
        assert result.session is not None
        assert not (result.session.worktree_path / ".dovo" / "run").exists()
        assert not (result.session.worktree_path / ".dovo" / "run").is_symlink()
        assert not paths.session_dir("dovo_independent").exists()

    def test_cleanup_of_worktree_containing_run_symlink_leaves_link_target_contents_intact(
        self, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        """[tier-1/integration] WorktreeLifecycle.cleanup: a worktree whose .dovo/run symlink points at an external directory holding sentinel.txt returns warnings == [], the worktree directory is removed, and sentinel.txt still reads its original content."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        create_result = lifecycle.create(session_id="dovo_linked_cleanup")
        assert create_result.session is not None
        session = create_result.session
        external_dir = tmp_path / "external-session"
        external_dir.mkdir()
        sentinel_path = external_dir / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        link_path = session.worktree_path / ".dovo" / "run"
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(external_dir, target_is_directory=True)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not session.worktree_path.exists()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"


class WorktreeLifecycleCapacityTests:
    """[tier-1/integration] WorktreeLifecycle._check_capacity, exercised through create()."""

    def test_create_at_capacity_returns_capacity_exceeded_without_creating_worktree(
        self, worktree_workspace: Path
    ) -> None:
        """[tier-1/integration] WorktreeLifecycle.create: DEFAULT_MAXIMUM_WORKTREES_ALLOWED (3) active worktree directories already exist -> CAPACITY_EXCEEDED, no fourth worktree or branch is created."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        for i in range(3):
            result = lifecycle.create(session_id=f"dovo_cap_{i}")
            assert result.status == WorktreeCreateStatus.OK

        result = lifecycle.create(session_id="dovo_cap_overflow")

        assert result.status == WorktreeCreateStatus.CAPACITY_EXCEEDED
        assert not (worktree_workspace / ".dovo" / "worktrees" / "dovo_cap_overflow").exists()
        assert "dovo/dovo_cap_overflow" not in GitRunner.list_branches(worktree_workspace)


class WorktreeLifecycleCleanupTests:
    """[tier-1/integration] WorktreeLifecycle.cleanup: worktree removal and branch deletion."""

    def test_cleanup_removes_dovo_directory_and_deletes_temporary_branch(self, worktree_workspace: Path) -> None:
        """[tier-1/integration] WorktreeLifecycle.cleanup: removes the worktree directory from disk and deletes its temporary dovo/<id> branch, with no warnings."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        create_result = lifecycle.create(session_id="dovo_cleanup_branch")
        assert create_result.session is not None
        session = create_result.session
        assert "dovo/dovo_cleanup_branch" in GitRunner.list_branches(worktree_workspace)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not session.worktree_path.exists()
        assert "dovo/dovo_cleanup_branch" not in GitRunner.list_branches(worktree_workspace)

    def test_cleanup_of_already_removed_worktree_is_idempotent_and_warning_free(self, worktree_workspace: Path) -> None:
        """[tier-1/integration] WorktreeLifecycle.cleanup: calling cleanup a second time after the worktree directory is already gone still deletes the branch (idempotent) and produces no warnings."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        create_result = lifecycle.create(session_id="dovo_cleanup_twice")
        assert create_result.session is not None
        session = create_result.session

        first_warnings = lifecycle.cleanup(session)
        second_warnings = lifecycle.cleanup(session)

        assert first_warnings == []
        assert second_warnings == []


class WorktreeLifecycleDiscardPartialTests:
    """[tier-1/integration] WorktreeLifecycle.discard_partial: best-effort cleanup after a failed create."""

    def test_discard_partial_removes_dovo_directory_and_branch(self, worktree_workspace: Path) -> None:
        """[tier-1/integration] WorktreeLifecycle.discard_partial: removes the given worktree directory and deletes the given branch."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        worktree_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_discard"
        temp_branch = "dovo/dovo_discard"
        GitRunner.worktree_add(worktree_workspace, worktree_path, temp_branch, "HEAD")
        assert worktree_path.is_dir()

        lifecycle.discard_partial(worktree_path, temp_branch)

        assert not worktree_path.exists()
        assert temp_branch not in GitRunner.list_branches(worktree_workspace)

    def test_discard_partial_on_nonexistent_branch_does_not_raise(self, worktree_workspace: Path) -> None:
        """[tier-1/integration] WorktreeLifecycle.discard_partial: a branch that was never created is a best-effort no-op, not an exception."""
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        lifecycle.discard_partial(worktree_workspace / ".dovo" / "worktrees" / "never-existed", "dovo/never-existed")
