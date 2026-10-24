"""Unit tests for dovo.core.diagnostics.checks.worktree_refs."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import WorktreesRepository
from dovo.core.diagnostics.checks.worktree_refs import WorktreeRefsCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from dovo.core.git.exceptions import GitCommandError
from dovo.core.git.runner import GitRunner
from tests.harness import WorkspaceBuilder
from tests.harness.fakes import FakeGitRunner

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
        check = WorktreeRefsCheck()
        paths = workspace_paths_factory(tmp_path, None)
        context = DiagnosticsContext(cwd=tmp_path, paths=paths, git=GitRunner())

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 0}
        assert not (resolve_global_paths(None).data_dir / "dovo.db").is_file()

    def test_execute_clean_workspace_returns_ok_with_verified_count(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=GitRunner())

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"verified_count": 0}

    def test_execute_stale_worktree_ref_returns_warning_stale(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        target = worktree_refs_workspace / ".dovo" / "worktrees" / "dovo_stale1"
        GitRunner().worktree_add(
            worktree_refs_workspace,
            target_path=target,
            branch="dovo/dovo_stale1",
            base_ref="main",
        )
        shutil.rmtree(target)
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=GitRunner())

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
        missing_path = worktree_refs_workspace / ".dovo" / "worktrees" / "dovo_missing"
        worktree_refs_repository.create(
            id="dovo_missing",
            branch_name="dovo/dovo_missing",
            base_commit="abc",
            worktree_path=missing_path,
        )
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=GitRunner())

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
        worktrees_dir = worktree_refs_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        (worktrees_dir / "dovo_clean").mkdir()
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=GitRunner())

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_WORKTREE_ORPHAN"
        assert result.details == {"orphan_directories": ["dovo_clean"]}
        assert result.warnings == ["1 orphaned worktree directory(s) detected."]

    def test_execute_scans_context_paths_worktrees_dir(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        """Return DOCTOR_WORKTREE_ORPHAN with details == {"orphan_directories": ["orphan_1"]} when orphan_1 sits under a relocated context.paths.worktrees_dir."""
        paths = worktree_refs_workspace_paths.model_copy(update={"worktrees_dir": tmp_path / "elsewhere"})
        orphan_dir = paths.worktrees_dir / "orphan_1"
        orphan_dir.mkdir(parents=True)
        GitRunner().run(["init"], orphan_dir)
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=paths, git=GitRunner())

        result = check.execute(context)

        assert result.status == CheckStatus.WARNING
        assert result.error_code == "DOCTOR_WORKTREE_ORPHAN"
        assert result.details == {"orphan_directories": ["orphan_1"]}

    def test_execute_stale_and_orphan_both_present_prioritizes_stale(
        self,
        worktree_refs_workspace: Path,
        worktree_refs_workspace_paths: WorkspacePaths,
        worktree_refs_repository: WorktreesRepository,
    ) -> None:
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
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=GitRunner())

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
    ) -> None:
        failure = GitCommandError(["git", "worktree", "list"], 1, "", "fatal error")
        fake = FakeGitRunner({("worktree", "list"): failure})
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=fake)

        result = check.execute(context)

        assert result.check_id == "worktree.refs"
        assert result.category == CheckCategory.WORKTREE
        assert result.status == CheckStatus.WARNING
        assert result.error_code is None
        assert result.details == {"detection_status": "git_failed"}
        assert result.warnings == ["Worktree detection could not complete (status='git_failed')."]


class WorktreeRefsCheckRepositoryConstructionTests:
    def test_execute_constructs_worktrees_repository_with_explicit_db_path_and_project_id(
        self, worktree_refs_workspace: Path, worktree_refs_workspace_paths: WorkspacePaths
    ) -> None:
        check = WorktreeRefsCheck()
        context = DiagnosticsContext(cwd=worktree_refs_workspace, paths=worktree_refs_workspace_paths, git=GitRunner())

        result = check.execute(context)

        assert result.status == CheckStatus.OK
