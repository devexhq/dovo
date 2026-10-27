"""Integration tests for GitRunner plumbing commands against real git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import Failure, catch_failure
from dovo.core.git.exceptions import GitError
from dovo.core.git.models import GitProcessOutput, GitWorktreeEntry
from dovo.core.git.runner import GitRunner
from tests.harness.diagnostic_log import read_single_event
from tests.harness.fakes import FakeGitRunner


class GitRunnerPlumbingTests:
    """Integration tests for GitRunner plumbing commands against real git repositories."""

    def test_worktree_add_creates_new_working_tree_and_branch(self, git_repo: Path, tmp_path: Path) -> None:
        worktree_path = tmp_path / "feature-worktree"

        GitRunner().worktree_add(git_repo, worktree_path, "feature-worktree", "main")

        assert worktree_path.is_dir() is True
        assert GitRunner().get_current_branch(worktree_path) == "feature-worktree"

        entries = GitRunner().worktree_list(git_repo)
        worktree_entries = [e for e in entries if e.path.resolve() == worktree_path.resolve()]
        assert len(worktree_entries) == 1
        assert worktree_entries[0] == GitWorktreeEntry(
            path=worktree_path.resolve(),
            head_sha=GitRunner().rev_parse(worktree_path, rev="HEAD"),
            branch="feature-worktree",
        )

    def test_worktree_remove_detaches_and_cleans_directory(self, git_repo: Path, tmp_path: Path) -> None:
        worktree_path = tmp_path / "feature-remove"
        GitRunner().worktree_add(git_repo, worktree_path, "feature-remove", "main")
        assert worktree_path.is_dir() is True

        GitRunner().worktree_remove(git_repo, worktree_path, force=True)

        assert worktree_path.exists() is False
        entries = GitRunner().worktree_list(git_repo)
        matching = [e for e in entries if e.path.resolve() == worktree_path.resolve()]
        assert matching == []

    def test_branch_exists_reports_present_and_absent_local_branches(self, git_repo: Path) -> None:
        GitRunner().run(["branch", "dovo/dovo_present"], path=git_repo)

        assert GitRunner().branch_exists(git_repo, "dovo/dovo_present") is True
        assert GitRunner().branch_exists(git_repo, "dovo/dovo_absent") is False

    def test_has_uncommitted_changes_detects_staged_and_unstaged_files(self, git_repo: Path) -> None:
        assert GitRunner().has_uncommitted_changes(git_repo) is False

        readme = git_repo / "README.md"
        readme.write_text("# Modified\n", encoding="utf-8")
        assert GitRunner().has_uncommitted_changes(git_repo) is True

        GitRunner().run(["add", "README.md"], path=git_repo)
        assert GitRunner().has_uncommitted_changes(git_repo) is True

        GitRunner().run(["commit", "-m", "Commit mod"], path=git_repo)
        assert GitRunner().has_uncommitted_changes(git_repo) is False

        new_file = git_repo / "untracked.txt"
        new_file.write_text("untracked\n", encoding="utf-8")
        assert GitRunner().has_uncommitted_changes(git_repo) is True

        new_file.unlink()
        assert GitRunner().has_uncommitted_changes(git_repo) is False

        readme.unlink()
        assert GitRunner().has_uncommitted_changes(git_repo) is True

    def test_get_current_branch_handles_detached_head_gracefully(self, git_repo: Path) -> None:
        GitRunner().run(["checkout", "--detach", "HEAD"], path=git_repo)

        assert GitRunner().get_current_branch(git_repo) == "HEAD (detached)"

    def test_commit_records_only_literal_paths_and_keeps_other_index_entries(self, git_repo: Path) -> None:
        (git_repo / "*.md").write_text("literal\n", encoding="utf-8")
        GitRunner().run(["--literal-pathspecs", "add", "*.md"], path=git_repo)
        (git_repo / "other.md").write_text("untracked\n", encoding="utf-8")
        (git_repo / "README.md").write_text("# Unstaged edit\n", encoding="utf-8")
        (git_repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        GitRunner().run(["add", "staged.txt"], path=git_repo)

        GitRunner().commit(git_repo, "Commit literal path", paths=["*.md"])

        committed = GitRunner().run(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], path=git_repo)
        assert committed == "*.md"
        assert GitRunner().status_porcelain(git_repo) == [" M README.md", "A  staged.txt", "?? other.md"]

    def test_dirty_paths_lists_unquoted_names_and_both_sides_of_rename(self, git_repo: Path) -> None:
        (git_repo / "orig.txt").write_text("rename me\n", encoding="utf-8")
        GitRunner().run(["add", "orig.txt"], path=git_repo)
        GitRunner().run(["commit", "-m", "Add orig.txt"], path=git_repo)
        GitRunner().run(["mv", "orig.txt", "moved.txt"], path=git_repo)
        (git_repo / "é_staged.txt").write_text("staged\n", encoding="utf-8")
        GitRunner().run(["add", "é_staged.txt"], path=git_repo)
        (git_repo / "é_untracked.txt").write_text("untracked\n", encoding="utf-8")
        readme = git_repo / "README.md"
        content = readme.read_text(encoding="utf-8")
        readme.unlink()
        (git_repo / "README.rst").write_text(content, encoding="utf-8")
        GitRunner().run(["add", "--intent-to-add", "README.rst"], path=git_repo)

        assert GitRunner().dirty_paths(git_repo) == [
            "README.md",
            "README.rst",
            "moved.txt",
            "orig.txt",
            "é_staged.txt",
            "é_untracked.txt",
        ]

    def test_patch_paths_lists_unquoted_names_and_both_sides_of_rename(self, git_repo: Path) -> None:
        base_sha = GitRunner().rev_parse(git_repo, rev="HEAD")
        GitRunner().run(["mv", "README.md", "é renamed.md"], path=git_repo)
        (git_repo / "new\tfile.txt").write_text("new\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=git_repo)
        diff_text = GitRunner().run(["diff", "--binary", "--cached", base_sha], path=git_repo)
        (git_repo / "unrelated.txt").write_text("not in the patch\n", encoding="utf-8")

        assert GitRunner().patch_paths(git_repo, diff_text) == ["README.md", "new\tfile.txt", "é renamed.md"]


class GitRunnerExecTests:
    def test_exec_returns_result_for_nonzero_exit(self, git_repo: Path) -> None:
        """Return GitProcessOutput(returncode=1, stdout="", stderr="") for `show-ref --verify --quiet refs/heads/absent` without raising."""
        result = GitRunner().exec(["show-ref", "--verify", "--quiet", "refs/heads/absent"], git_repo)

        assert result == GitProcessOutput(returncode=1, stdout="", stderr="")

    def test_exec_raises_not_found_when_git_is_missing(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Raise GitError GIT_BINARY_MISSING "Git execution failed ('git status'): git not found" when PATH holds no git."""
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec(["status"], git_repo)

        assert str(excinfo.value) == "Git execution failed ('git status'): git not found"
        assert excinfo.value.error_code == ErrorCode.GIT_BINARY_MISSING

    def test_missing_git_caught_with_catch_failure_leaves_no_event_file(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Copy GIT_BINARY_MISSING and the message into caught.failure with no diagnostic, and create no logs directory."""
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))

        with catch_failure("worktree.create") as caught:
            GitRunner().exec(["status"], git_repo)

        assert caught.failure == Failure(
            ErrorCode.GIT_BINARY_MISSING, "Git execution failed ('git status'): git not found", (), None
        )
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_missing_working_directory_raises_fs_not_found_and_is_recorded(
        self, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Report FS_NOT_FOUND naming the directory, not GIT_BINARY_MISSING, and record the OSError cause when path does not exist."""
        missing = tmp_path / "gone"

        with catch_failure("worktree.create") as caught:
            GitRunner().exec(["status"], missing)

        assert caught.failure is not None
        assert caught.failure.error_code == ErrorCode.FS_NOT_FOUND
        assert caught.failure.message == (
            f"Git execution failed ('git status'): working directory '{missing}' does not exist"
        )
        assert caught.failure.diagnostic is not None
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "worktree.create"

    def test_exec_raises_timeout_ending_git_timeout(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raise GitError GIT_TIMEOUT whose message ends "('git status') (GIT_TIMEOUT)" when dovo.core.git.runner.subprocess.run raises TimeoutExpired."""

        def _time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=1)

        monkeypatch.setattr("dovo.core.git.runner.subprocess.run", _time_out)

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec(["status"], git_repo)

        assert str(excinfo.value).endswith("('git status') (GIT_TIMEOUT)")
        assert excinfo.value.error_code == ErrorCode.GIT_TIMEOUT

    def test_run_raises_command_error_on_nonzero_exit_when_checked(self, git_repo: Path) -> None:
        """Raise GitError GIT_FAILED "Git execution failed ('git rev-parse --verify nope'): fatal: Needed a single revision" from run."""
        with pytest.raises(GitError) as excinfo:
            GitRunner().run(["rev-parse", "--verify", "nope"], git_repo)

        assert excinfo.value.message == (
            "Git execution failed ('git rev-parse --verify nope'): fatal: Needed a single revision"
        )
        assert excinfo.value.error_code == ErrorCode.GIT_FAILED

    def test_run_returns_stdout_on_nonzero_exit_when_unchecked(self, git_repo: Path) -> None:
        """Return "" from run(["show-ref", "--verify", "--quiet", "refs/heads/absent"], check=False)."""
        output = GitRunner().run(["show-ref", "--verify", "--quiet", "refs/heads/absent"], git_repo, check=False)

        assert output == ""

    def test_branch_exists_raises_git_failed_on_fatal_show_ref(self, tmp_path: Path) -> None:
        """Raise GitError GIT_FAILED naming the show-ref command and its stderr when show-ref exits 128."""
        fake = FakeGitRunner({("show-ref",): GitProcessOutput(128, "", "fatal: bad")})

        with pytest.raises(GitError) as excinfo:
            fake.branch_exists(tmp_path, "x")

        assert excinfo.value.error_code is ErrorCode.GIT_FAILED
        assert (
            excinfo.value.message == "Git execution failed ('git show-ref --verify --quiet refs/heads/x'): fatal: bad"
        )

    def test_get_current_branch_propagates_fs_not_found(self, tmp_path: Path) -> None:
        """Raise the GitError(FS_NOT_FOUND) git branch --show-current raised instead of returning "unknown"."""
        failure = GitError(ErrorCode.FS_NOT_FOUND, "working directory does not exist")
        fake = FakeGitRunner({("branch", "--show-current"): failure})

        with pytest.raises(GitError) as excinfo:
            fake.get_current_branch(tmp_path)

        assert excinfo.value is failure

    @pytest.mark.parametrize(
        "code", [pytest.param(ErrorCode.GIT_TIMEOUT, id="timeout"), pytest.param(ErrorCode.FS_NOT_FOUND, id="fs")]
    )
    def test_status_porcelain_propagates_codes_it_does_not_swallow(self, tmp_path: Path, code: ErrorCode) -> None:
        """Raise the GitError(code) git status raised instead of returning []."""
        failure = GitError(code, "boom")
        fake = FakeGitRunner({("status",): failure})

        with pytest.raises(GitError) as excinfo:
            fake.status_porcelain(tmp_path)

        assert excinfo.value is failure

    @pytest.mark.parametrize(
        ("method", "key", "expected_args"),
        [
            pytest.param("apply_check", ("apply", "--check"), ("apply", "--check", "--binary", "-"), id="apply_check"),
            pytest.param("apply", ("apply", "--binary"), ("apply", "--binary", "-"), id="apply"),
        ],
    )
    def test_apply_methods_run_through_exec(
        self, tmp_path: Path, method: str, key: tuple[str, ...], expected_args: tuple[str, ...]
    ) -> None:
        """Return (1, "", "error: patch failed: a.txt:1") from the FakeGitRunner response and record calls == [expected_args]."""
        fake = FakeGitRunner({key: GitProcessOutput(1, "", "error: patch failed: a.txt:1")})

        result = getattr(fake, method)(tmp_path, "diff --git a/a.txt b/a.txt")

        assert result == (1, "", "error: patch failed: a.txt:1")
        assert fake.calls == [expected_args]
