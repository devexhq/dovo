"""Unit tests for dovo.core.diagnostics.checks.git_repo."""

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.diagnostics.checks.git_repo import GitRepoCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from dovo.core.git import GitError, GitNotFoundError, GitProcessOutput
from dovo.core.git.runner import GitRunner
from tests.harness.fakes import FakeGitRunner

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

_FS_NOT_FOUND = GitError(ErrorCode.FS_NOT_FOUND, "working directory does not exist")


class GitRepoCheckTests:
    """Unit tests for GitRepoCheck diagnostic outcomes."""

    def test_execute_missing_git_binary_returns_binary_missing_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: None)
        check = GitRepoCheck()
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=GitRunner())

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_BINARY_MISSING"
        assert result.errors == ["git binary was not found on PATH."]

    def test_execute_git_disappears_after_which_returns_binary_missing_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): GitNotFoundError("git not found")})
        check = GitRepoCheck()
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=fake)

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_BINARY_MISSING"
        assert result.errors == ["git binary was not found on PATH."]

    @pytest.mark.parametrize(
        "responses",
        [
            pytest.param({("rev-parse", "--is-inside-work-tree"): _FS_NOT_FOUND}, id="inside_work_tree"),
            pytest.param(
                {
                    ("rev-parse", "--is-inside-work-tree"): GitProcessOutput(0, "true\n", ""),
                    ("rev-parse", "--show-toplevel"): _FS_NOT_FOUND,
                },
                id="show_toplevel",
            ),
        ],
    )
    def test_execute_propagates_fs_not_found_from_either_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
        responses: dict[tuple[str, ...], GitProcessOutput | Exception],
    ) -> None:
        """Raise the GitError(FS_NOT_FOUND) a rev-parse probe raised instead of returning a failed check."""
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")
        context = DiagnosticsContext(
            cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=FakeGitRunner(responses)
        )

        with pytest.raises(GitError) as excinfo:
            GitRepoCheck().execute(context)

        assert excinfo.value is _FS_NOT_FOUND

    def test_execute_outside_git_repository_returns_not_repo_failure(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        check = GitRepoCheck()
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=GitRunner())

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_NOT_REPO"
        assert result.errors == [f"'{tmp_path}' is not a Git repository."]

    def test_execute_inside_git_repository_returns_ok_with_root_and_branch(
        self, git_repo: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        check = GitRepoCheck()
        context = DiagnosticsContext(cwd=git_repo, paths=workspace_paths_factory(git_repo, None), git=GitRunner())

        result = check.execute(context)

        resolved_root = str(git_repo.resolve())
        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {"root": resolved_root, "branch": "main"}
