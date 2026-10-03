"""Integration tests for worktree storage bridge lifecycle behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree.models import WorktreeCreateStatus
from dovo.core.worktree.services import lifecycle as lifecycle_module
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from tests.harness import WorkspaceBuilder


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a worktree database."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


def _paths(workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for workspace, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None))


def _save_project_identity(workspace: Path) -> None:
    """Persist the fixed project identity used by storage bridge tests."""
    identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    save_project_identity(workspace / ".dovo" / "project.json", identity)


class WorktreeLifecycleStorageBridgeTests:
    """Integration tests for worktree session storage bridges."""

    def test_create_with_project_identity_creates_run_symlink_to_global_session_directory(
        self, monkeypatch: pytest.MonkeyPatch, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        """An identified worktree links its runtime bridge to global session storage."""
        global_root = tmp_path / "global"
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        _save_project_identity(worktree_workspace)
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_bridge_626")

        bridge_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_bridge_626" / ".dovo" / "run"
        session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "dovo_bridge_626"
        assert result.status == WorktreeCreateStatus.OK
        assert bridge_path.is_symlink()
        assert bridge_path.resolve() == session_dir
        assert session_dir.is_dir()
        assert not (worktree_workspace / ".dovo" / "sessions" / "dovo_bridge_626").exists()

    def test_cleanup_unlinks_run_symlink_and_preserves_global_session_contents(
        self, monkeypatch: pytest.MonkeyPatch, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        """Cleanup removes only the bridge and leaves global session contents intact."""
        global_root = tmp_path / "global"
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        _save_project_identity(worktree_workspace)
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        result = lifecycle.create(session_id="dovo_bridge_626")
        assert result.session is not None
        bridge_path = result.session.worktree_path / ".dovo" / "run"
        sentinel_path = bridge_path.resolve() / "sentinel.txt"
        Filesystem.atomic_write_text(sentinel_path, "preserve me")

        warnings = lifecycle.cleanup(result.session)

        assert warnings == []
        assert not bridge_path.is_symlink()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"

    def test_create_with_stale_broken_run_symlink_replaces_it_with_session_target(
        self, monkeypatch: pytest.MonkeyPatch, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        """A tracked broken bridge is replaced with the selected session target."""
        global_root = tmp_path / "global"
        source_bridge = worktree_workspace / ".dovo" / "run"
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        _save_project_identity(worktree_workspace)
        source_bridge.symlink_to(tmp_path / "missing-session")
        GitRunner.run(["add", "-f", ".dovo/run"], path=worktree_workspace)
        GitRunner.run(["commit", "-m", "Add stale storage bridge"], path=worktree_workspace)
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_bridge_626")

        bridge_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_bridge_626" / ".dovo" / "run"
        session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "dovo_bridge_626"
        assert result.status == WorktreeCreateStatus.OK
        assert bridge_path.is_symlink()
        assert bridge_path.resolve() == session_dir

    def test_create_when_symlink_creation_is_unsupported_returns_ok_with_bridge_warning(
        self, monkeypatch: pytest.MonkeyPatch, worktree_workspace: Path
    ) -> None:
        """A Windows privilege limitation returns a successful worktree with a warning."""

        def raise_symlink_error(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise OSError(1314, "symlink privilege unavailable")

        monkeypatch.setattr(lifecycle_module.platform, "system", lambda: "Windows")
        monkeypatch.setattr(Path, "symlink_to", raise_symlink_error)
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_bridge_626")

        bridge_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_bridge_626" / ".dovo" / "run"
        assert result.status == WorktreeCreateStatus.OK
        assert len(result.warnings) == 1
        assert "symlink privilege unavailable" in result.warnings[0]
        assert (worktree_workspace / ".dovo" / "worktrees" / "dovo_bridge_626").is_dir()
        assert not bridge_path.exists()

    def test_create_when_symlink_creation_fails_returns_storage_bridge_failed_and_discards_partial_worktree(
        self, monkeypatch: pytest.MonkeyPatch, worktree_workspace: Path
    ) -> None:
        """An operational symlink failure removes the newly created worktree and branch."""

        def raise_symlink_error(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise OSError("storage device I/O failure")

        monkeypatch.setattr(Path, "symlink_to", raise_symlink_error)
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_bridge_626")

        worktree_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_bridge_626"
        assert result.status == WorktreeCreateStatus.STORAGE_BRIDGE_FAILED
        assert not worktree_path.exists()
        assert "dovo/dovo_bridge_626" not in GitRunner.list_branches(worktree_workspace)
        assert (
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("dovo_bridge_626") is None
        )

    def test_create_with_regular_run_directory_returns_storage_bridge_failed_without_target_deletion(
        self, worktree_workspace: Path
    ) -> None:
        """A worktree-side bridge collision discards only the partial worktree, never the source branch's committed content or already-persisted session storage."""
        source_sentinel = worktree_workspace / ".dovo" / "run" / "sentinel.txt"
        Filesystem.atomic_write_text(source_sentinel, "do not delete")
        GitRunner.run(["add", "-f", ".dovo/run/sentinel.txt"], path=worktree_workspace)
        GitRunner.run(["commit", "-m", "Add storage bridge collision"], path=worktree_workspace)
        session_dir = worktree_workspace / ".dovo" / "sessions" / "dovo_bridge_626"
        preexisting_session_file = session_dir / "run.json"
        Filesystem.atomic_write_text(preexisting_session_file, "preserve me too")
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_bridge_626")

        worktree_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_bridge_626"
        assert result.status == WorktreeCreateStatus.STORAGE_BRIDGE_FAILED
        assert source_sentinel.read_text(encoding="utf-8") == "do not delete"
        assert preexisting_session_file.read_text(encoding="utf-8") == "preserve me too"
        assert not worktree_path.exists()
        assert "dovo/dovo_bridge_626" not in GitRunner.list_branches(worktree_workspace)
        assert (
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("dovo_bridge_626") is None
        )


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
