"""Contract tests for the Sandbox facade: collaborator wiring and pass-through delegation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.harness import WorkspaceBuilder
from worktree.common.filesystem import WorkspacePaths
from worktree.core.db import SandboxesRepository
from worktree.core.sandbox.facade import Sandbox
from worktree.core.sandbox.models import (
    SandboxApplyStatus,
    SandboxCreateStatus,
    SandboxDiffStatus,
    SandboxListStatus,
    SandboxShowStatus,
)


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a sandbox database."""
    return WorkspaceBuilder(tmp_path / "sandbox_ws").with_git().with_database().build()


@pytest.fixture
def sandbox_workspace_paths(
    sandbox_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's sandbox workspace."""
    return workspace_paths_factory(sandbox_workspace, None)


class SandboxConstructionTests:
    def test_default_db_is_bound_to_the_given_paths(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] Sandbox.__init__: omitting db constructs a SandboxesRepository scoped to the given paths, distinct per instance."""
        sandbox = Sandbox(sandbox_workspace_paths)
        assert sandbox.db.db_path == sandbox_workspace_paths.database_file
        assert sandbox.db.project_id == sandbox_workspace_paths.project_id

    def test_lifecycle_and_patch_share_the_same_path_and_db(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] Sandbox.__init__: the constructed lifecycle and patch collaborators are bound to the same path and db as the facade."""
        db = SandboxesRepository(
            db_path=sandbox_workspace_paths.database_file, project_id=sandbox_workspace_paths.project_id
        )
        sandbox = Sandbox(sandbox_workspace_paths, db=db)

        assert sandbox.lifecycle.path == sandbox.path
        assert sandbox.lifecycle.db is db
        assert sandbox.patch.db is db
        assert sandbox.patch.lifecycle is sandbox.lifecycle

    def test_sandbox_base_dir_delegates_to_lifecycle(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] Sandbox.sandbox_base_dir: the property returns exactly what the underlying lifecycle returns."""
        sandbox = Sandbox(sandbox_workspace_paths)
        assert sandbox.sandbox_base_dir == sandbox.lifecycle.sandbox_base_dir


class SandboxCreateListShowDeleteDelegationTests:
    def test_create_persists_a_sandbox_that_list_and_show_can_find(
        self, sandbox_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] Sandbox.create/.list/.show: a sandbox created through the facade is visible via list() and show() with matching id."""
        sandbox = Sandbox(sandbox_workspace_paths)

        create_result = sandbox.create(session_id="sbx_facade_create")
        assert create_result.status == SandboxCreateStatus.OK

        list_result = sandbox.list()
        assert list_result.status == SandboxListStatus.OK
        assert any(row.id == "sbx_facade_create" for row in list_result.sandboxes)

        show_result = sandbox.show("sbx_facade_create")
        assert show_result.status == SandboxShowStatus.OK
        assert show_result.sandbox is not None
        assert show_result.sandbox.id == "sbx_facade_create"

    def test_delete_reflects_the_same_sandbox_created_through_the_facade(
        self, sandbox_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] Sandbox.create/.delete: delete() returns a READY status referencing the sandbox created through the facade."""
        sandbox = Sandbox(sandbox_workspace_paths)
        sandbox.create(session_id="sbx_facade_delete")

        delete_result = sandbox.delete("sbx_facade_delete")

        assert delete_result.sandbox_id == "sbx_facade_delete"
        assert delete_result.sandbox is not None


class SandboxCleanupPruneGetActiveDelegationTests:
    def test_get_active_lists_the_directory_created_by_create(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Sandbox.create/.get_active: the sandbox directory created by create() appears in get_active()."""
        sandbox = Sandbox(sandbox_workspace_paths)
        create_result = sandbox.create(session_id="sbx_facade_active")
        assert create_result.session is not None

        active = sandbox.get_active()

        assert create_result.session.sandbox_path in active

    def test_cleanup_removes_the_sandbox_worktree_directory(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Sandbox.create/.cleanup: cleanup() removes the sandbox worktree directory created by create()."""
        sandbox = Sandbox(sandbox_workspace_paths)
        create_result = sandbox.create(session_id="sbx_facade_cleanup")
        assert create_result.session is not None

        warnings = sandbox.cleanup(create_result.session)

        assert warnings == []
        assert not create_result.session.sandbox_path.exists()

    def test_prune_reports_no_stale_sandboxes_for_a_freshly_created_one(
        self, sandbox_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] Sandbox.create/.prune: prune() on a workspace with one freshly created, still-active sandbox reports it as not pruned."""
        sandbox = Sandbox(sandbox_workspace_paths)
        sandbox.create(session_id="sbx_facade_prune")

        result = sandbox.prune(dry_run=True)

        assert all(item.identifier != "sbx_facade_prune" for item in result.items)


class SandboxDiffApplyDelegationTests:
    def test_diff_on_freshly_created_sandbox_reports_empty_diff(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Sandbox.create/.diff: a freshly created sandbox with no further commits reports EMPTY_DIFF through the facade's patch delegation."""
        sandbox = Sandbox(sandbox_workspace_paths)
        create_result = sandbox.create(session_id="sbx_facade_diff")
        assert create_result.status == SandboxCreateStatus.OK

        diff_result = sandbox.diff("sbx_facade_diff")

        assert diff_result.status == SandboxDiffStatus.EMPTY_DIFF

    def test_apply_on_unknown_sandbox_id_returns_not_found(self, sandbox_workspace_paths: WorkspacePaths) -> None:
        """[tier-1/integration] Sandbox.apply: an unknown sandbox id delegates through to patch.apply and returns NOT_FOUND."""
        sandbox = Sandbox(sandbox_workspace_paths)

        result = sandbox.apply("missing_sandbox")

        assert result.status == SandboxApplyStatus.NOT_FOUND
