"""Tests for the git probe through Status.collect()."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.db import SessionsRepository
from dovo.core.git import GitError, GitProcessOutput, GitRunner, git_command_error
from dovo.core.status import Status, WorkspaceHealth
from dovo.core.status.models import GitStatusInfo
from tests.harness.diagnostic_log import diagnostic_record
from tests.harness.fakes import FakeGitRunner
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths

_GIT_TIMEOUT_MESSAGE = "Git timed out after 120s ('git rev-parse --is-inside-work-tree')"
_GIT_MISSING_MESSAGE = "Git execution failed ('git rev-parse --is-inside-work-tree'): git not found"
_GIT_MESSAGES = {ErrorCode.GIT_TIMEOUT: _GIT_TIMEOUT_MESSAGE, ErrorCode.GIT_BINARY_MISSING: _GIT_MISSING_MESSAGE}
_GIT_TIMEOUT_REMEDIATION = Remediation(
    message=(
        "Check for git lock files, credential prompts, or stuck git processes, then retry, "
        "or raise 'DOVO_GIT_TIMEOUT_SECONDS' for a slow repository."
    )
)


class GitProbeTests:
    """Tests for git repository status collection."""

    def test_collect_dirty_worktree_counts_uncommitted_files(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(branch="feature-dirty")

        untracked = workspace / "new_file.txt"
        untracked.write_text("hello", encoding="utf-8")

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

        assert result.git.branch == "feature-dirty"
        assert result.git.is_dirty is True
        assert result.git.uncommitted_files == 1
        assert result.warnings == ["Working tree has 1 uncommitted change(s)."]

    def test_collect_detached_head_reports_detached_branch(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(branch="feature-detached")

        commit_hash = GitRunner().run(["rev-parse", "HEAD"], path=workspace).strip()
        subprocess.run(["git", "checkout", commit_hash], cwd=workspace, check=True, capture_output=True)

        result = Status(
            initialized_workspace_paths(workspace),
            git=GitRunner(),
            sessions_db=initialized_workspace_db(workspace).sessions,
        ).collect()

        assert result.git.branch == "HEAD (detached)"
        assert result.git.is_git_repo is True

    def test_collect_non_git_directory_reports_not_a_repo(self, tmp_path: Path) -> None:
        non_git_dir = tmp_path / "non_git"
        non_git_dir.mkdir(parents=True, exist_ok=True)
        paths = initialized_workspace_paths(non_git_dir)
        sessions_db = SessionsRepository(db_path=tmp_path / "reconcile.db", project_id=paths.project_id)

        result = Status(paths, git=GitRunner(), sessions_db=sessions_db).collect()

        assert result.is_initialized is False
        assert result.git.is_git_repo is False
        assert result.git.branch == "none"
        assert result.config.is_valid is False
        assert result.database.exists is False
        assert result.warnings == ["Dovo workspace is not initialized. Run 'dovo init' to configure."]
        assert result.remediations == [
            Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init"),
            Remediation(message="Run 'git init' or navigate to a Git repository.", command="git init"),
        ]

    @pytest.mark.parametrize(
        ("responses", "error_code", "remediation"),
        [
            pytest.param(
                {("rev-parse", "--is-inside-work-tree"): GitError(ErrorCode.GIT_TIMEOUT, _GIT_TIMEOUT_MESSAGE)},
                ErrorCode.GIT_TIMEOUT,
                _GIT_TIMEOUT_REMEDIATION,
                id="timeout_inside_work_tree",
            ),
            pytest.param(
                {
                    ("rev-parse", "--is-inside-work-tree"): GitProcessOutput(0, "true\n", ""),
                    ("branch", "--show-current"): GitError(ErrorCode.GIT_TIMEOUT, _GIT_TIMEOUT_MESSAGE),
                },
                ErrorCode.GIT_TIMEOUT,
                _GIT_TIMEOUT_REMEDIATION,
                id="timeout_branch",
            ),
            pytest.param(
                {
                    ("rev-parse", "--is-inside-work-tree"): GitProcessOutput(0, "true\n", ""),
                    ("branch", "--show-current"): GitProcessOutput(0, "feature\n", ""),
                    ("status", "--porcelain"): GitError(ErrorCode.GIT_TIMEOUT, _GIT_TIMEOUT_MESSAGE),
                },
                ErrorCode.GIT_TIMEOUT,
                _GIT_TIMEOUT_REMEDIATION,
                id="timeout_status",
            ),
            pytest.param(
                {("rev-parse", "--is-inside-work-tree"): GitError(ErrorCode.GIT_BINARY_MISSING, _GIT_MISSING_MESSAGE)},
                ErrorCode.GIT_BINARY_MISSING,
                Remediation(message="Install Git and make sure 'git' is on PATH."),
                id="binary_missing",
            ),
        ],
    )
    def test_collect_unreadable_git_reports_its_code_warning_and_record(
        self,
        status_workspace: BuildStatusWorkspace,
        frozen_diagnostic_clock: Path,
        responses: dict[tuple[str, ...], GitProcessOutput | Exception],
        error_code: ErrorCode,
        remediation: Remediation,
    ) -> None:
        """Report a degraded status with git's code, its message as the warning, its own remediation and one record."""
        workspace = status_workspace()
        fake = FakeGitRunner(responses)

        result = Status(
            initialized_workspace_paths(workspace), git=fake, sessions_db=initialized_workspace_db(workspace).sessions
        ).collect()

        record = diagnostic_record(frozen_diagnostic_clock)
        assert result.status is WorkspaceHealth.DEGRADED
        assert result.git == GitStatusInfo(
            is_git_repo=False,
            branch="unknown",
            is_dirty=False,
            uncommitted_files=0,
            error_code=error_code,
            warning=_GIT_MESSAGES[error_code],
            diagnostic=record,
        )
        assert result.warnings == [_GIT_MESSAGES[error_code]]
        assert result.remediations == [remediation]
        assert result.diagnostics == [record]

    def test_collect_git_command_error_reports_no_branch(self, status_workspace: BuildStatusWorkspace) -> None:
        """Return is_git_repo False, branch "none", the git init remediation and no record when the fake raises a GIT_FAILED GitError for rev-parse --is-inside-work-tree."""
        workspace = status_workspace()
        failure = git_command_error(
            ["git", "rev-parse", "--is-inside-work-tree"], 128, "", "fatal: not a git repository"
        )
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): failure})

        result = Status(
            initialized_workspace_paths(workspace), git=fake, sessions_db=initialized_workspace_db(workspace).sessions
        ).collect()

        assert result.git.is_git_repo is False
        assert result.git.branch == "none"
        assert result.git.error_code is None
        assert result.remediations == [
            Remediation(message="Run 'git init' or navigate to a Git repository.", command="git init")
        ]
        assert result.diagnostics == []

    def test_collect_propagates_git_workdir_missing(self, status_workspace: BuildStatusWorkspace) -> None:
        """Raise the GitError(GIT_WORKDIR_MISSING) rev-parse --is-inside-work-tree raised instead of reporting a git status."""
        workspace = status_workspace()
        failure = GitError(ErrorCode.GIT_WORKDIR_MISSING, "working directory does not exist")
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): failure})
        status = Status(
            initialized_workspace_paths(workspace), git=fake, sessions_db=initialized_workspace_db(workspace).sessions
        )

        with pytest.raises(GitError) as excinfo:
            status.collect()

        assert excinfo.value is failure

    def test_collect_rev_parse_not_true_reports_not_a_repo(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace()
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): GitProcessOutput(0, "false\n", "")})

        result = Status(
            initialized_workspace_paths(workspace), git=fake, sessions_db=initialized_workspace_db(workspace).sessions
        ).collect()

        assert result.git.is_git_repo is False
        assert result.git.branch == "none"
        assert result.remediations == [
            Remediation(message="Run 'git init' or navigate to a Git repository.", command="git init")
        ]
