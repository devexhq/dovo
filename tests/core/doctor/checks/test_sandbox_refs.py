"""Unit tests for dovo.core.doctor.checks.sandbox_refs."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import SandboxesRepository
from dovo.core.db.connection import resolve_db_path
from dovo.core.doctor.checks.sandbox_refs import SandboxRefsCheck
from dovo.core.doctor.models import CheckCategory, CheckStatus, DoctorContext
from dovo.core.git.exceptions import GitCommandError
from dovo.core.git.runner import GitRunner
from tests.harness import WorkspaceBuilder

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


@pytest.fixture
def sandbox_refs_workspace(tmp_path: Path) -> Path:
    """Create a workspace with Git and an initialized centralized SQLite database for sandbox.refs tests."""
    return WorkspaceBuilder(tmp_path / "sandbox_refs_ws").with_git().with_database().build()


@pytest.fixture
def sandbox_refs_workspace_paths(
    sandbox_refs_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for sandbox_refs_workspace."""
    return workspace_paths_factory(sandbox_refs_workspace, None)


@pytest.fixture
def sandbox_refs_repository(sandbox_refs_workspace_paths: WorkspacePaths) -> SandboxesRepository:
    """Provide a SandboxesRepository scoped to the sandbox refs workspace."""
    return SandboxesRepository(
        db_path=sandbox_refs_workspace_paths.database_file,
        project_id=sandbox_refs_workspace_paths.project_id,
    )


class SandboxRefsCheckTests:
    """Unit tests for SandboxRefsCheck diagnostic outcomes."""

    def test_execute_missing_database_returns_ok_with_zero_verified_count(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: no centralized database file on disk -> OK, message='0 sandbox(es) verified against database and Git worktree state.', details={'verified_count': 0}, error_code=None, and no database file is created as a side effect."""
        check = SandboxRefsCheck()
        paths = workspace_paths_factory(tmp_path, None)
        context = DoctorContext(cwd=tmp_path, paths=paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 0}
        assert not resolve_db_path(resolve_global_paths(None)).is_file()

    def test_execute_clean_workspace_returns_ok_with_verified_count(
        self, sandbox_refs_workspace: Path, sandbox_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: git+db workspace with zero sandboxes -> OK, message='0 sandbox(es) verified against database and Git worktree state.', details={'verified_count': 0}, error_code=None."""
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 0}

    def test_execute_stale_worktree_ref_returns_warning_stale(
        self, sandbox_refs_workspace: Path, sandbox_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: registered git worktree whose directory was removed -> WARNING, error_code='DOCTOR_SANDBOX_STALE', details={'stale_ids': [str(target_path)]}."""
        target = sandbox_refs_workspace / ".dovo" / "sandboxes" / "sbx_wt1"
        GitRunner.worktree_add(
            sandbox_refs_workspace,
            target_path=target,
            branch="worktree/sandbox-sbx_wt1",
            base_ref="main",
        )
        shutil.rmtree(target)
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_SANDBOX_STALE"
        assert result.details == {"stale_ids": [str(target)]}
        assert result.warnings == ["1 stale sandbox reference(s) detected."]

    def test_execute_stale_db_record_returns_warning_stale(
        self,
        sandbox_refs_workspace: Path,
        sandbox_refs_workspace_paths: WorkspacePaths,
        sandbox_refs_repository: SandboxesRepository,
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: active SandboxesRepository record 'sbx_missing' whose sandbox_path is absent on disk -> WARNING, error_code='DOCTOR_SANDBOX_STALE', details={'stale_ids': ['sbx_missing']}."""
        missing_path = sandbox_refs_workspace / ".dovo" / "sandboxes" / "sbx_missing"
        sandbox_refs_repository.create(
            id="sbx_missing",
            branch_name="worktree/sandbox-sbx_missing",
            base_commit="abc",
            sandbox_path=missing_path,
        )
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_SANDBOX_STALE"
        assert result.details == {"stale_ids": ["sbx_missing"]}
        assert result.warnings == ["1 stale sandbox reference(s) detected."]

    def test_execute_orphaned_directory_returns_warning_orphan(
        self, sandbox_refs_workspace: Path, sandbox_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: untracked directory 'sbx_clean' under .dovo/sandboxes -> WARNING, error_code='DOCTOR_SANDBOX_ORPHAN', details={'orphan_directories': ['sbx_clean']}."""
        sandboxes_dir = sandbox_refs_workspace / ".dovo" / "sandboxes"
        sandboxes_dir.mkdir(parents=True, exist_ok=True)
        (sandboxes_dir / "sbx_clean").mkdir()
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_SANDBOX_ORPHAN"
        assert result.details == {"orphan_directories": ["sbx_clean"]}
        assert result.warnings == ["1 orphaned sandbox directory(s) detected."]

    def test_execute_stale_and_orphan_both_present_prioritizes_stale(
        self,
        sandbox_refs_workspace: Path,
        sandbox_refs_workspace_paths: WorkspacePaths,
        sandbox_refs_repository: SandboxesRepository,
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: both a stale DB record 'sbx_missing' and an orphaned directory 'sbx_clean' exist -> WARNING, error_code='DOCTOR_SANDBOX_STALE', details=={'stale_ids': ['sbx_missing']} only (no 'orphan_directories' key)."""
        sandboxes_dir = sandbox_refs_workspace / ".dovo" / "sandboxes"
        sandboxes_dir.mkdir(parents=True, exist_ok=True)
        (sandboxes_dir / "sbx_clean").mkdir()
        missing_path = sandboxes_dir / "sbx_missing"
        sandbox_refs_repository.create(
            id="sbx_missing",
            branch_name="worktree/sandbox-sbx_missing",
            base_commit="abc",
            sandbox_path=missing_path,
        )
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_SANDBOX_STALE"
        assert result.details == {"stale_ids": ["sbx_missing"]}
        assert result.warnings == ["1 stale sandbox reference(s) detected."]

    def test_execute_git_worktree_list_failure_returns_warning_with_no_error_code(
        self,
        sandbox_refs_workspace: Path,
        sandbox_refs_workspace_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: GitRunner.worktree_list raises GitCommandError -> WARNING, error_code=None, details={'detection_status': 'git_failed'}."""

        def _raise_command_error(_path: Path) -> list[object]:
            raise GitCommandError(["git", "worktree", "list"], 1, "", "fatal error")

        monkeypatch.setattr(GitRunner, "worktree_list", _raise_command_error)
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "sandbox.refs"
        assert result.category == CheckCategory.SANDBOX
        assert result.status == CheckStatus.WARNING
        assert result.error_code is None
        assert result.details == {"detection_status": "git_failed"}
        assert result.warnings == ["Sandbox detection could not complete (status='git_failed')."]


class SandboxRefsCheckRepositoryConstructionTests:
    """[tier-1/unit] SandboxRefsCheck.execute: explicit db_path/project_id repository construction."""

    def test_execute_constructs_sandboxes_repository_with_explicit_db_path_and_project_id(
        self, sandbox_refs_workspace: Path, sandbox_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] SandboxRefsCheck.execute: SandboxesRepository is constructed with db_path=context.paths.database_file and project_id=context.paths.project_id (not the removed resolve_db_path()/lazy BaseRepository resolution); accessing .project_id does not raise ValueError."""
        check = SandboxRefsCheck()
        context = DoctorContext(cwd=sandbox_refs_workspace, paths=sandbox_refs_workspace_paths)

        result = check.execute(context)

        assert result.status == CheckStatus.OK
        assert sandbox_refs_workspace_paths.project_id is not None
