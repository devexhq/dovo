"""Unit tests for dovo.core.diagnostics.checks.git_repo."""

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.diagnostics.checks.git_repo import GitRepoCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from dovo.core.git import GitError, GitProcessOutput, git_command_error
from dovo.core.git.runner import GitRunner
from tests.harness.diagnostic_log import diagnostic_record
from tests.harness.fakes import FakeGitRunner

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]

_GIT_WORKDIR_MISSING = GitError(ErrorCode.GIT_WORKDIR_MISSING, "working directory does not exist")
_GIT_TIMEOUT = GitError(ErrorCode.GIT_TIMEOUT, "Git timed out after 120s ('git rev-parse --is-inside-work-tree')")
_INSIDE = GitProcessOutput(0, "true\n", "")


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
        fake = FakeGitRunner(
            {("rev-parse", "--is-inside-work-tree"): GitError(ErrorCode.GIT_BINARY_MISSING, "git not found")}
        )
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
            pytest.param({("rev-parse", "--is-inside-work-tree"): _GIT_WORKDIR_MISSING}, id="inside_work_tree"),
            pytest.param(
                {
                    ("rev-parse", "--is-inside-work-tree"): GitProcessOutput(0, "true\n", ""),
                    ("rev-parse", "--show-toplevel"): _GIT_WORKDIR_MISSING,
                },
                id="show_toplevel",
            ),
        ],
    )
    def test_execute_propagates_git_workdir_missing_from_either_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
        responses: dict[tuple[str, ...], GitProcessOutput | Exception],
    ) -> None:
        """Raise the GitError(GIT_WORKDIR_MISSING) a rev-parse probe raised instead of returning a failed check."""
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")
        context = DiagnosticsContext(
            cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=FakeGitRunner(responses)
        )

        with pytest.raises(GitError) as excinfo:
            GitRepoCheck().execute(context)

        assert excinfo.value is _GIT_WORKDIR_MISSING

    @pytest.mark.parametrize(
        "responses",
        [
            pytest.param({("rev-parse", "--is-inside-work-tree"): _GIT_TIMEOUT}, id="inside_work_tree"),
            pytest.param(
                {("rev-parse", "--is-inside-work-tree"): _INSIDE, ("rev-parse", "--show-toplevel"): _GIT_TIMEOUT},
                id="show_toplevel",
            ),
            pytest.param(
                {
                    ("rev-parse", "--is-inside-work-tree"): _INSIDE,
                    ("rev-parse", "--show-toplevel"): GitProcessOutput(0, "/repo\n", ""),
                    ("branch", "--show-current"): _GIT_TIMEOUT,
                },
                id="branch_show_current",
            ),
        ],
    )
    def test_execute_git_timeout_returns_timeout_failure_with_record(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
        frozen_diagnostic_clock: Path,
        responses: dict[tuple[str, ...], GitProcessOutput | Exception],
    ) -> None:
        """Fail with DOCTOR_GIT_TIMEOUT and the record of the timeout when any git call of the check times out."""
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")
        context = DiagnosticsContext(
            cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=FakeGitRunner(responses)
        )

        result = GitRepoCheck().execute(context)

        message = f"Git timed out in '{tmp_path}': {_GIT_TIMEOUT.message}"
        assert result.status == CheckStatus.FAILED
        assert result.error_code == ErrorCode.DOCTOR_GIT_TIMEOUT
        assert result.message == message
        assert result.errors == [message]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]

    def test_execute_show_toplevel_binary_missing_returns_binary_missing_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        workspace_paths_factory: WorkspacePathsFactory,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """Fail with DOCTOR_GIT_BINARY_MISSING, not DOCTOR_GIT_REPO_NOT_FOUND, when git vanishes before --show-toplevel."""
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")
        fake = FakeGitRunner(
            {
                ("rev-parse", "--is-inside-work-tree"): _INSIDE,
                ("rev-parse", "--show-toplevel"): GitError(ErrorCode.GIT_BINARY_MISSING, "git not found"),
            }
        )
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=fake)

        result = GitRepoCheck().execute(context)

        assert result.error_code == ErrorCode.DOCTOR_GIT_BINARY_MISSING
        assert result.errors == ["git binary was not found on PATH."]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]

    def test_execute_git_failed_returns_not_repo_failure_without_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Fail with DOCTOR_GIT_REPO_NOT_FOUND and no record when rev-parse --show-toplevel exits non-zero."""
        monkeypatch.setattr("dovo.core.diagnostics.checks.git_repo.shutil.which", lambda _name: "/usr/bin/git")
        failure = git_command_error(["rev-parse", "--show-toplevel"], 128, "", "fatal: not a git repository")
        fake = FakeGitRunner(
            {("rev-parse", "--is-inside-work-tree"): _INSIDE, ("rev-parse", "--show-toplevel"): failure}
        )
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=fake)

        result = GitRepoCheck().execute(context)

        assert result.error_code == ErrorCode.DOCTOR_GIT_REPO_NOT_FOUND
        assert result.errors == [f"'{tmp_path}' is not a Git repository."]
        assert result.diagnostics == []

    def test_execute_outside_git_repository_returns_not_repo_failure(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        check = GitRepoCheck()
        context = DiagnosticsContext(cwd=tmp_path, paths=workspace_paths_factory(tmp_path, None), git=GitRunner())

        result = check.execute(context)

        assert result.check_id == "git.repo"
        assert result.category == CheckCategory.GIT
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_GIT_REPO_NOT_FOUND"
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
