"""Tests for the git probe through Status.collect()."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.models import Remediation
from dovo.core.db import SessionsRepository
from dovo.core.git import GitCommandError, GitNotFoundError, GitPlumbingTimeoutError, GitProcessOutput, GitRunner
from dovo.core.status import Status
from tests.harness.fakes import FakeGitRunner
from tests.harness.status_workspace import BuildStatusWorkspace
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


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
            Remediation(message="Run 'dovo init' to initialize Dovo in this repository.", command="dovo init"),
            Remediation(message="Run 'git init' or navigate to a Git repository.", command="git init"),
        ]

    @pytest.mark.parametrize(
        "git_error",
        [
            pytest.param(GitNotFoundError("git not found"), id="not_found"),
            pytest.param(GitPlumbingTimeoutError("git timed out"), id="timeout"),
        ],
    )
    def test_collect_git_error_reports_unknown_branch(
        self, status_workspace: BuildStatusWorkspace, git_error: Exception
    ) -> None:
        """Return is_git_repo False, branch "unknown" and remediations == [Remediation(message="Run 'git init' or navigate to a Git repository.", command="git init")] when the fake raises git_error for rev-parse --is-inside-work-tree."""
        workspace = status_workspace()
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): git_error})

        result = Status(
            initialized_workspace_paths(workspace), git=fake, sessions_db=initialized_workspace_db(workspace).sessions
        ).collect()

        assert result.git.is_git_repo is False
        assert result.git.branch == "unknown"
        assert result.remediations == [
            Remediation(message="Run 'git init' or navigate to a Git repository.", command="git init")
        ]

    def test_collect_git_command_error_reports_no_branch(self, status_workspace: BuildStatusWorkspace) -> None:
        """Return is_git_repo False and branch "none" when the fake raises GitCommandError for rev-parse --is-inside-work-tree."""
        workspace = status_workspace()
        failure = GitCommandError(["git", "rev-parse", "--is-inside-work-tree"], 128, "", "fatal: not a git repository")
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): failure})

        result = Status(
            initialized_workspace_paths(workspace), git=fake, sessions_db=initialized_workspace_db(workspace).sessions
        ).collect()

        assert result.git.is_git_repo is False
        assert result.git.branch == "none"

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
