"""Integration tests for sandbox storage bridge lifecycle behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.harness import WorkspaceBuilder
from worktree.common.filesystem import Filesystem
from worktree.core.db import SandboxesRepository
from worktree.core.git.runner import GitRunner
from worktree.core.project.models import ProjectIdentity
from worktree.core.project.services.identity import save_project_identity
from worktree.core.sandbox.models import SandboxCreateStatus
from worktree.core.sandbox.services import lifecycle as lifecycle_module
from worktree.core.sandbox.services.lifecycle import SandboxLifecycle


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a sandbox database."""
    return WorkspaceBuilder(tmp_path / "sandbox_ws").with_git().with_database().build()


def _save_project_identity(workspace: Path) -> None:
    """Persist the fixed project identity used by storage bridge tests."""
    identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    save_project_identity(workspace / ".worktree" / "project.json", identity)


class SandboxLifecycleStorageBridgeTests:
    """Integration tests for sandbox session storage bridges."""

    def test_create_with_project_identity_creates_run_symlink_to_global_session_directory(
        self, monkeypatch: pytest.MonkeyPatch, sandbox_workspace: Path, tmp_path: Path
    ) -> None:
        """An identified sandbox links its runtime bridge to global session storage."""
        global_root = tmp_path / "global"
        monkeypatch.setenv("WORKTREE_HOME", str(global_root))
        _save_project_identity(sandbox_workspace)
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = lifecycle.create(session_id="sbx_bridge_626")

        bridge_path = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_bridge_626" / ".worktree" / "run"
        session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "sbx_bridge_626"
        assert result.status == SandboxCreateStatus.OK
        assert bridge_path.is_symlink()
        assert bridge_path.resolve() == session_dir
        assert session_dir.is_dir()
        assert not (sandbox_workspace / ".worktree" / "sessions" / "sbx_bridge_626").exists()

    def test_cleanup_unlinks_run_symlink_and_preserves_global_session_contents(
        self, monkeypatch: pytest.MonkeyPatch, sandbox_workspace: Path, tmp_path: Path
    ) -> None:
        """Cleanup removes only the bridge and leaves global session contents intact."""
        global_root = tmp_path / "global"
        monkeypatch.setenv("WORKTREE_HOME", str(global_root))
        _save_project_identity(sandbox_workspace)
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))
        result = lifecycle.create(session_id="sbx_bridge_626")
        assert result.session is not None
        bridge_path = result.session.sandbox_path / ".worktree" / "run"
        sentinel_path = bridge_path.resolve() / "sentinel.txt"
        Filesystem.atomic_write_text(sentinel_path, "preserve me")

        warnings = lifecycle.cleanup(result.session)

        assert warnings == []
        assert not bridge_path.is_symlink()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"

    def test_create_with_stale_broken_run_symlink_replaces_it_with_session_target(
        self, monkeypatch: pytest.MonkeyPatch, sandbox_workspace: Path, tmp_path: Path
    ) -> None:
        """A tracked broken bridge is replaced with the selected session target."""
        global_root = tmp_path / "global"
        source_bridge = sandbox_workspace / ".worktree" / "run"
        monkeypatch.setenv("WORKTREE_HOME", str(global_root))
        _save_project_identity(sandbox_workspace)
        source_bridge.symlink_to(tmp_path / "missing-session")
        GitRunner.run(["add", "-f", ".worktree/run"], path=sandbox_workspace)
        GitRunner.run(["commit", "-m", "Add stale storage bridge"], path=sandbox_workspace)
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = lifecycle.create(session_id="sbx_bridge_626")

        bridge_path = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_bridge_626" / ".worktree" / "run"
        session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "sbx_bridge_626"
        assert result.status == SandboxCreateStatus.OK
        assert bridge_path.is_symlink()
        assert bridge_path.resolve() == session_dir

    def test_create_when_symlink_creation_is_unsupported_returns_ok_with_bridge_warning(
        self, monkeypatch: pytest.MonkeyPatch, sandbox_workspace: Path
    ) -> None:
        """A Windows privilege limitation returns a successful sandbox with a warning."""

        def raise_symlink_error(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise OSError(1314, "symlink privilege unavailable")

        monkeypatch.setattr(lifecycle_module.platform, "system", lambda: "Windows")
        monkeypatch.setattr(Path, "symlink_to", raise_symlink_error)
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = lifecycle.create(session_id="sbx_bridge_626")

        bridge_path = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_bridge_626" / ".worktree" / "run"
        assert result.status == SandboxCreateStatus.OK
        assert len(result.warnings) == 1
        assert "symlink privilege unavailable" in result.warnings[0]
        assert (sandbox_workspace / ".worktree" / "sandboxes" / "sbx_bridge_626").is_dir()
        assert not bridge_path.exists()

    def test_create_when_symlink_creation_fails_returns_storage_bridge_failed_and_discards_partial_sandbox(
        self, monkeypatch: pytest.MonkeyPatch, sandbox_workspace: Path
    ) -> None:
        """An operational symlink failure removes the newly created worktree and branch."""

        def raise_symlink_error(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise OSError("storage device I/O failure")

        monkeypatch.setattr(Path, "symlink_to", raise_symlink_error)
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = lifecycle.create(session_id="sbx_bridge_626")

        sandbox_path = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_bridge_626"
        assert result.status == SandboxCreateStatus.STORAGE_BRIDGE_FAILED
        assert not sandbox_path.exists()
        assert "worktree/sandbox-sbx_bridge_626" not in GitRunner.list_branches(sandbox_workspace)
        assert SandboxesRepository(sandbox_workspace).get("sbx_bridge_626") is None

    def test_create_with_regular_run_directory_returns_storage_bridge_failed_without_target_deletion(
        self, sandbox_workspace: Path
    ) -> None:
        """A sandbox-side bridge collision discards only the partial sandbox, never the source branch's committed content or already-persisted session storage."""
        source_sentinel = sandbox_workspace / ".worktree" / "run" / "sentinel.txt"
        Filesystem.atomic_write_text(source_sentinel, "do not delete")
        GitRunner.run(["add", "-f", ".worktree/run/sentinel.txt"], path=sandbox_workspace)
        GitRunner.run(["commit", "-m", "Add storage bridge collision"], path=sandbox_workspace)
        session_dir = sandbox_workspace / ".worktree" / "sessions" / "sbx_bridge_626"
        preexisting_session_file = session_dir / "run.json"
        Filesystem.atomic_write_text(preexisting_session_file, "preserve me too")
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        result = lifecycle.create(session_id="sbx_bridge_626")

        sandbox_path = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_bridge_626"
        assert result.status == SandboxCreateStatus.STORAGE_BRIDGE_FAILED
        assert source_sentinel.read_text(encoding="utf-8") == "do not delete"
        assert preexisting_session_file.read_text(encoding="utf-8") == "preserve me too"
        assert not sandbox_path.exists()
        assert "worktree/sandbox-sbx_bridge_626" not in GitRunner.list_branches(sandbox_workspace)
        assert SandboxesRepository(sandbox_workspace).get("sbx_bridge_626") is None


class SandboxLifecycleCapacityTests:
    """[tier-1/integration] SandboxLifecycle._check_capacity, exercised through create()."""

    def test_create_at_capacity_returns_capacity_exceeded_without_creating_worktree(
        self, sandbox_workspace: Path
    ) -> None:
        """[tier-1/integration] SandboxLifecycle.create: DEFAULT_MAXIMUM_SANDBOXES_ALLOWED (3) active sandbox directories already exist -> CAPACITY_EXCEEDED, no fourth worktree or branch is created."""
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))
        for i in range(3):
            result = lifecycle.create(session_id=f"sbx_cap_{i}")
            assert result.status == SandboxCreateStatus.OK

        result = lifecycle.create(session_id="sbx_cap_overflow")

        assert result.status == SandboxCreateStatus.CAPACITY_EXCEEDED
        assert not (sandbox_workspace / ".worktree" / "sandboxes" / "sbx_cap_overflow").exists()
        assert "worktree/sandbox-sbx_cap_overflow" not in GitRunner.list_branches(sandbox_workspace)


class SandboxLifecycleCleanupTests:
    """[tier-1/integration] SandboxLifecycle.cleanup: worktree removal and branch deletion."""

    def test_cleanup_removes_worktree_directory_and_deletes_temporary_branch(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxLifecycle.cleanup: removes the sandbox worktree directory from disk and deletes its temporary worktree/sandbox-<id> branch, with no warnings."""
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))
        create_result = lifecycle.create(session_id="sbx_cleanup_branch")
        assert create_result.session is not None
        session = create_result.session
        assert "worktree/sandbox-sbx_cleanup_branch" in GitRunner.list_branches(sandbox_workspace)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not session.sandbox_path.exists()
        assert "worktree/sandbox-sbx_cleanup_branch" not in GitRunner.list_branches(sandbox_workspace)

    def test_cleanup_of_already_removed_worktree_is_idempotent_and_warning_free(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxLifecycle.cleanup: calling cleanup a second time after the worktree directory is already gone still deletes the branch (idempotent) and produces no warnings."""
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))
        create_result = lifecycle.create(session_id="sbx_cleanup_twice")
        assert create_result.session is not None
        session = create_result.session

        first_warnings = lifecycle.cleanup(session)
        second_warnings = lifecycle.cleanup(session)

        assert first_warnings == []
        assert second_warnings == []


class SandboxLifecycleDiscardPartialTests:
    """[tier-1/integration] SandboxLifecycle.discard_partial: best-effort cleanup after a failed create."""

    def test_discard_partial_removes_worktree_directory_and_branch(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxLifecycle.discard_partial: removes the given worktree directory and deletes the given branch."""
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))
        sandbox_path = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_discard"
        temp_branch = "worktree/sandbox-sbx_discard"
        GitRunner.worktree_add(sandbox_workspace, sandbox_path, temp_branch, "HEAD")
        assert sandbox_path.is_dir()

        lifecycle.discard_partial(sandbox_path, temp_branch)

        assert not sandbox_path.exists()
        assert temp_branch not in GitRunner.list_branches(sandbox_workspace)

    def test_discard_partial_on_nonexistent_branch_does_not_raise(self, sandbox_workspace: Path) -> None:
        """[tier-1/integration] SandboxLifecycle.discard_partial: a branch that was never created is a best-effort no-op, not an exception."""
        lifecycle = SandboxLifecycle(sandbox_workspace, SandboxesRepository(sandbox_workspace))

        lifecycle.discard_partial(
            sandbox_workspace / ".worktree" / "sandboxes" / "never-existed", "worktree/sandbox-never-existed"
        )
