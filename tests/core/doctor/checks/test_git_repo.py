"""Unit tests for worktree.core.doctor.checks.git_repo."""

from collections.abc import Callable
from pathlib import Path

import pytest

from worktree.common.filesystem import WorkspacePaths
from worktree.core.doctor.checks.git_repo import GitRepoCheck
from worktree.core.doctor.models import CheckCategory, CheckStatus, DoctorContext
from worktree.core.git import GitNotFoundError

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class GitRepoCheckTests:
    """Unit tests for GitRepoCheck diagnostic outcomes."""

    def test_execute_missing_git_binary_returns_binary_missing_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] GitRepoCheck.execute: shutil.which('git') is None -> FAILED with DOCTOR_GIT_BINARY_MISSING."""
        monkeypatch.setattr("worktree.core.doctor.checks.git_repo.shutil.which", lambda _name: None)
        check = GitRepoCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_BINARY_MISSING"
        assert result.errors == ["git binary was not found on PATH."]

    def test_execute_git_disappears_after_which_returns_binary_missing_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] GitRepoCheck.execute: shutil.which finds git but GitRunner.run raises GitNotFoundError -> FAILED with DOCTOR_GIT_BINARY_MISSING."""
        monkeypatch.setattr("worktree.core.doctor.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")

        def _raise_not_found(*_args: object, **_kwargs: object) -> str:
            raise GitNotFoundError("git not found")

        monkeypatch.setattr("worktree.core.doctor.checks.git_repo.GitRunner.run", _raise_not_found)
        check = GitRepoCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_BINARY_MISSING"
        assert result.errors == ["git binary was not found on PATH."]

    def test_execute_outside_git_repository_returns_not_repo_failure(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] GitRepoCheck.execute: context.cwd has no .git -> FAILED with DOCTOR_GIT_NOT_REPO."""
        check = GitRepoCheck()
        context = DoctorContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None))

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_NOT_REPO"
        assert result.errors == [f"'{tmp_path}' is not a Git repository."]

    def test_execute_inside_git_repository_returns_ok_with_root_and_branch(
        self, git_repo: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] GitRepoCheck.execute: context.cwd=git_repo (main, HEAD resolvable) -> OK with root and branch."""
        check = GitRepoCheck()
        context = DoctorContext(cwd=git_repo, paths=workspace_paths_factory(git_repo, None))

        result = check.execute(context)

        resolved_root = str(git_repo.resolve())
        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"root": resolved_root, "branch": "main"}
