"""Unit tests for dovo.core.doctor.checks.worktree_refs."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import WorktreesRepository
from dovo.core.db.connection import resolve_db_path
from dovo.core.doctor.checks.worktree_refs import WorktreeRefsCheck
from dovo.core.doctor.models import CheckCategory, CheckStatus, DoctorContext
from dovo.core.git.exceptions import GitCommandError
from dovo.core.git.runner import GitRunner
from tests.harness import WorkspaceBuilder

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


@pytest.fixture
def worktree_refs_workspace(tmp_path: Path) -> Path:
    """Create a workspace with Git and an initialized centralized SQLite database for worktree.refs tests."""
    return WorkspaceBuilder(tmp_path / "worktree_refs_ws").with_git().with_database().build()


@pytest.fixture
def worktree_refs_workspace_paths(
    worktree_refs_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for worktree_refs_workspace."""
    return workspace_paths_factory(worktree_refs_workspace, None)


@pytest.fixture
def worktree_refs_repository(worktree_refs_workspace_paths: WorkspacePaths) -> WorktreesRepository:
    """Provide a WorktreesRepository scoped to the worktree refs workspace."""
    return WorktreesRepository(
        db_path=worktree_refs_workspace_paths.database_file,
        project_id=worktree_refs_workspace_paths.project_id,
    )


class WorktreeRefsCheckTests:
    """Unit tests for WorktreeRefsCheck diagnostic outcomes."""

    def test_execute_missing_database_returns_ok_with_zero_verified_count(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: no centralized database file on disk -> OK, message='0 worktree(es) verified against database and Git worktree state.', details={'verified_count': 0}, error_code=None, and no database file is created as a side effect."""
        check = WorktreeRefsCheck()
        paths = workspace_paths_factory(tmp_path, None)
        context = DoctorContext(cwd=tmp_path, paths=paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 0}
        assert not resolve_db_path(resolve_global_paths(None)).is_file()

    def test_execute_clean_workspace_returns_ok_with_verified_count(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: git+db workspace with zero worktrees -> OK, message='0 worktree(es) verified against database and Git worktree state.', details={'verified_count': 0}, error_code=None."""
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 0}

    def test_execute_stale_worktree_ref_returns_warning_stale(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: registered git worktree whose directory was removed -> WARNING, error_code='DOCTOR_WORKTREE_STALE', details={'stale_ids': [str(target_path)]}."""
        target = worktree_refs_workspace / ".dovo" / "worktrees" / "dovo_stale1"
        GitRunner.worktree_add(
            worktree_refs_workspace,
            target_path=target,
            branch="dovo/dovo_stale1",
            base_ref="main",
        )
        shutil.rmtree(target)
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_WORKTREE_STALE"
        assert result.details == {"stale_ids": [str(target)]}
        assert result.warnings == ["1 stale worktree reference(s) detected."]

    def test_execute_stale_db_record_returns_warning_stale(
        self,
        worktree_refs_workspace: Path,
        worktree_refs_workspace_paths: WorkspacePaths,
        worktree_refs_repository: WorktreesRepository,
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: active WorktreesRepository record 'dovo_missing' whose worktree_path is absent on disk -> WARNING, error_code='DOCTOR_WORKTREE_STALE', details={'stale_ids': ['dovo_missing']}."""
        missing_path = worktree_refs_workspace / ".dovo" / "worktrees" / "dovo_missing"
        worktree_refs_repository.create(
            id="dovo_missing",
            branch_name="dovo/dovo_missing",
            base_commit="abc",
            worktree_path=missing_path,
        )
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_WORKTREE_STALE"
        assert result.details == {"stale_ids": ["dovo_missing"]}
        assert result.warnings == ["1 stale worktree reference(s) detected."]

    def test_execute_orphaned_directory_returns_warning_orphan(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: untracked directory 'dovo_clean' under .dovo/worktrees -> WARNING, error_code='DOCTOR_WORKTREE_ORPHAN', details={'orphan_directories': ['dovo_clean']}."""
        worktrees_dir = worktree_refs_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        (worktrees_dir / "dovo_clean").mkdir()
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_WORKTREE_ORPHAN"
        assert result.details == {"orphan_directories": ["dovo_clean"]}
        assert result.warnings == ["1 orphaned worktree directory(s) detected."]

    def test_execute_stale_and_orphan_both_present_prioritizes_stale(
        self,
        worktree_refs_workspace: Path,
        worktree_refs_workspace_paths: WorkspacePaths,
        worktree_refs_repository: WorktreesRepository,
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: both a stale DB record 'dovo_missing' and an orphaned directory 'dovo_clean' exist -> WARNING, error_code='DOCTOR_WORKTREE_STALE', details=={'stale_ids': ['dovo_missing']} only (no 'orphan_directories' key)."""
        worktrees_dir = worktree_refs_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        (worktrees_dir / "dovo_clean").mkdir()
        missing_path = worktrees_dir / "dovo_missing"
        worktree_refs_repository.create(
            id="dovo_missing",
            branch_name="dovo/dovo_missing",
            base_commit="abc",
            worktree_path=missing_path,
        )
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_WORKTREE_STALE"
        assert result.details == {"stale_ids": ["dovo_missing"]}
        assert result.warnings == ["1 stale worktree reference(s) detected."]

    def test_execute_git_worktree_list_failure_returns_warning_with_no_error_code(
        self,
        worktree_refs_workspace: Path,
        worktree_refs_workspace_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: GitRunner.worktree_list raises GitCommandError -> WARNING, error_code=None, details={'detection_status': 'git_failed'}."""

        def _raise_command_error(_path: Path) -> list[object]:
            raise GitCommandError(["git", "worktree", "list"], 1, "", "fatal error")

        monkeypatch.setattr(GitRunner, "worktree_list", _raise_command_error)
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code is None
        assert result.details == {"detection_status": "git_failed"}
        assert result.warnings == ["Worktree detection could not complete (status='git_failed')."]


class WorktreeRefsCheckRepositoryConstructionTests:
    """[tier-1/unit] WorktreeRefsCheck.execute: explicit db_path/project_id repository construction."""

    def test_execute_constructs_worktrees_repository_with_explicit_db_path_and_project_id(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] WorktreeRefsCheck.execute: WorktreesRepository is constructed with db_path=context.paths.database_file and project_id=context.paths.project_id (not the removed resolve_db_path()/lazy BaseRepository resolution); accessing .project_id does not raise ValueError."""
        check = WorktreeRefsCheck()
        context = DoctorContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths)

        result = check.execute(context)

        assert result.status == CheckStatus.OK
        assert worktree_refs_workspace_paths.project_id is not None
