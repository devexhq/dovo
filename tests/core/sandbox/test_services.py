from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.constants import DEFAULT_MAXIMUM_SANDBOXES_ALLOWED
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SandboxesRepository, SandboxStatus
from dovo.core.git.runner import GitRunner
from dovo.core.sandbox.models import (
    SandboxCreateStatus,
    SandboxDeleteStatus,
    SandboxListStatus,
    SandboxShowStatus,
)
from dovo.core.sandbox.services.delete import collect_sandbox_delete
from dovo.core.sandbox.services.lifecycle import SandboxLifecycle
from dovo.core.sandbox.services.list import collect_sandbox_list
from dovo.core.sandbox.services.show import collect_sandbox_show
from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "sandbox_ws").with_git().with_database().build()


@pytest.fixture
def sandbox_workspace_paths(
    sandbox_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's sandbox workspace."""
    return workspace_paths_factory(sandbox_workspace, None)


@pytest.fixture
def workspace_paths_no_identity(
    tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve a workspace snapshot without project identity for guard-path tests."""
    return workspace_paths_factory(tmp_path / "uninitialized", None)


class SandboxCreationTests:
    """Integration tests verifying worktree creation and metadata recording."""

    def test_create_initializes_worktree_and_branch_metadata(
        self,
        sandbox_workspace: Path,
        sandbox_workspace_paths: WorkspacePaths,
    ) -> None:
        """Create initializes worktree on disk, git branch, and DB row."""
        db = SandboxesRepository(
            db_path=sandbox_workspace_paths.database_file, project_id=sandbox_workspace_paths.project_id
        )
        lifecycle = SandboxLifecycle(sandbox_workspace_paths, db)

        result = lifecycle.create(session_id="sbx_test001", name="test-sandbox")

        expected_sandbox_path = (sandbox_workspace / ".dovo" / "sandboxes" / "sbx_test001").resolve()
        expected_branch = "worktree/sandbox-sbx_test001"
        head_commit = GitRunner.rev_parse(sandbox_workspace, rev="HEAD")

        assert result.status == SandboxCreateStatus.OK
        assert result.session is not None
        assert result.session.session_id == "sbx_test001"
        assert result.session.target_branch == expected_branch
        assert result.session.sandbox_path == expected_sandbox_path
        assert result.session.base_commit == head_commit
        assert result.session.name == "test-sandbox"
        assert result.session.created_at is not None
        assert result.session.command_passed is None
        assert result.session.wip_applied is False
        assert result.session.wip_paths == []
        assert result.warnings == []
        assert result.errors == []
        assert result.fixes == []
        assert expected_sandbox_path.is_dir()
        assert expected_branch in GitRunner.list_branches(sandbox_workspace)

        record = db.get("sbx_test001")
        assert record is not None
        assert record.id == "sbx_test001"
        assert record.name == "test-sandbox"
        assert record.branch_name == expected_branch
        assert record.base_commit == head_commit
        assert record.sandbox_path == expected_sandbox_path
        assert record.status == SandboxStatus.ACTIVE
        assert record.created_at is not None
        assert record.updated_at is not None


class SandboxCapacityTests:
    """Integration tests verifying capacity ceiling enforcement."""

    def test_create_enforces_max_active_sandboxes_limit(
        self,
        sandbox_workspace: Path,
        sandbox_workspace_paths: WorkspacePaths,
    ) -> None:
        """Creating a sandbox beyond max_active_sandboxes returns CAPACITY_EXCEEDED."""
        db = SandboxesRepository(
            db_path=sandbox_workspace_paths.database_file, project_id=sandbox_workspace_paths.project_id
        )
        lifecycle = SandboxLifecycle(sandbox_workspace_paths, db)

        assert DEFAULT_MAXIMUM_SANDBOXES_ALLOWED >= 1
        for idx in range(DEFAULT_MAXIMUM_SANDBOXES_ALLOWED):
            lifecycle.create(f"sbx_cap_{idx + 1}")

        overflow_id = f"sbx_cap_{DEFAULT_MAXIMUM_SANDBOXES_ALLOWED + 1}"
        overflow = lifecycle.create(overflow_id)

        assert overflow.status == SandboxCreateStatus.CAPACITY_EXCEEDED
        assert overflow.session is None
        assert overflow.errors == [
            f"Maximum active sandboxes reached ({DEFAULT_MAXIMUM_SANDBOXES_ALLOWED}/{DEFAULT_MAXIMUM_SANDBOXES_ALLOWED})."
        ]
        assert overflow.warnings == []
        assert overflow.fixes == [
            "Run `dovo prune` to remove stale sandboxes, or",
            "Raise sandbox.max_active_sandboxes in .dovo/config.json",
        ]
        assert not (sandbox_workspace / ".dovo" / "sandboxes" / overflow_id).exists()
        assert f"worktree/sandbox-{overflow_id}" not in GitRunner.list_branches(sandbox_workspace)
        assert db.get(overflow_id) is None


class SandboxNotInitializedGateTests:
    """[tier-1/unit] Defensive status gates avoid database and filesystem access."""

    def test_sandbox_create_returns_not_initialized_without_touching_disk_or_db(
        self, workspace_paths_no_identity: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create: absent project identity returns NOT_INITIALIZED before repository creation."""

        def _unexpected_create(self: SandboxesRepository, *args: object, **kwargs: object) -> None:
            raise AssertionError("database must not be used")

        monkeypatch.setattr(SandboxesRepository, "create", _unexpected_create)
        db = SandboxesRepository(
            db_path=workspace_paths_no_identity.database_file, project_id=workspace_paths_no_identity.project_id
        )

        result = SandboxLifecycle(workspace_paths_no_identity, db).create("sbx_no_identity")

        assert result.status == SandboxCreateStatus.NOT_INITIALIZED
        assert not workspace_paths_no_identity.sandboxes_dir.exists()

    def test_sandbox_list_returns_not_initialized_without_querying_db(
        self, workspace_paths_no_identity: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list: absent project identity skips both reconciliation and row lookup."""

        def _unexpected_query(self: SandboxesRepository, *args: object, **kwargs: object) -> None:
            raise AssertionError("database must not be queried")

        monkeypatch.setattr(SandboxesRepository, "reconcile_stale_active", _unexpected_query)
        monkeypatch.setattr(SandboxesRepository, "list", _unexpected_query)
        db = SandboxesRepository(
            db_path=workspace_paths_no_identity.database_file, project_id=workspace_paths_no_identity.project_id
        )

        result = collect_sandbox_list(workspace_paths_no_identity, db)

        assert result.status == SandboxListStatus.NOT_INITIALIZED
        assert result.sandboxes == []

    def test_sandbox_show_and_delete_return_not_initialized_without_querying_db(
        self, workspace_paths_no_identity: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """show/delete: absent project identity skips the repository lookup for both status variants."""

        def _unexpected_get(self: SandboxesRepository, *args: object, **kwargs: object) -> None:
            raise AssertionError("database must not be queried")

        monkeypatch.setattr(SandboxesRepository, "get", _unexpected_get)
        db = SandboxesRepository(
            db_path=workspace_paths_no_identity.database_file, project_id=workspace_paths_no_identity.project_id
        )

        show_result = collect_sandbox_show(workspace_paths_no_identity, db, "sbx_no_identity")
        delete_result = collect_sandbox_delete(workspace_paths_no_identity, db, sandbox_id="sbx_no_identity")

        assert show_result.status == SandboxShowStatus.NOT_INITIALIZED
        assert delete_result.status == SandboxDeleteStatus.NOT_INITIALIZED
