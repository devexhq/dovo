"""Integration tests for GitRunner plumbing commands against real git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import Failure, catch_failure
from dovo.core.git.exceptions import GitError
from dovo.core.git.models import GitBytesOutput, GitProcessOutput, GitWorktreeEntry
from dovo.core.git.runner import GitRunner, extract_conflicts
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

    def test_missing_working_directory_raises_git_workdir_missing_and_is_recorded(
        self, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Report GIT_WORKDIR_MISSING naming the directory, not GIT_BINARY_MISSING, and record the OSError cause when path does not exist."""
        missing = tmp_path / "gone"

        with catch_failure("worktree.create") as caught:
            GitRunner().exec(["status"], missing)

        assert caught.failure is not None
        assert caught.failure.error_code == ErrorCode.GIT_WORKDIR_MISSING
        assert caught.failure.message == (
            f"Git execution failed ('git status'): working directory '{missing}' does not exist"
        )
        assert caught.failure.diagnostic is not None
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "worktree.create"

    def test_exec_raises_git_timeout_naming_the_command(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raise GitError GIT_TIMEOUT whose message ends "('git status')" when dovo.core.git.runner.subprocess.run raises TimeoutExpired."""

        def _time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=1)

        monkeypatch.setattr("dovo.core.git.runner.subprocess.run", _time_out)

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec(["status"], git_repo)

        assert str(excinfo.value).endswith("('git status')")
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

    def test_get_current_branch_propagates_git_workdir_missing(self, tmp_path: Path) -> None:
        """Raise the GitError(GIT_WORKDIR_MISSING) git branch --show-current raised instead of returning "unknown"."""
        failure = GitError(ErrorCode.GIT_WORKDIR_MISSING, "working directory does not exist")
        fake = FakeGitRunner({("branch", "--show-current"): failure})

        with pytest.raises(GitError) as excinfo:
            fake.get_current_branch(tmp_path)

        assert excinfo.value is failure

    def test_get_current_branch_propagates_git_timeout(self, tmp_path: Path) -> None:
        """Raise the GitError(GIT_TIMEOUT) git branch --show-current raised instead of returning "unknown"."""
        failure = GitError(ErrorCode.GIT_TIMEOUT, "Git timed out after 120s ('git branch --show-current')")
        fake = FakeGitRunner({("branch", "--show-current"): failure})

        with pytest.raises(GitError) as excinfo:
            fake.get_current_branch(tmp_path)

        assert excinfo.value is failure

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(GitError(ErrorCode.GIT_BINARY_MISSING, "git not found"), id="binary_missing"),
            pytest.param(GitProcessOutput(128, "", "fatal: not a git repository"), id="git_failed"),
        ],
    )
    def test_get_current_branch_returns_unknown_when_git_is_missing_or_fails(
        self, tmp_path: Path, failure: GitProcessOutput | Exception
    ) -> None:
        """Return "unknown" when git branch --show-current finds no git binary or exits non-zero."""
        fake = FakeGitRunner({("branch", "--show-current"): failure})

        assert fake.get_current_branch(tmp_path) == "unknown"

    @pytest.mark.parametrize(
        ("override", "timeout", "named"),
        [
            pytest.param(None, 120, "120s", id="unset"),
            pytest.param("1.5", 1.5, "1.5s", id="override"),
            pytest.param("soon", 120, "120s", id="not_a_number"),
            pytest.param("0", 120, "120s", id="not_positive"),
            pytest.param("nan", 120, "120s", id="nan"),
            pytest.param("inf", 120, "120s", id="infinite"),
            pytest.param("1e300", 120, "120s", id="huge"),
            pytest.param("86400", 86400, "86400s", id="one_day"),
        ],
    )
    def test_exec_bounds_git_by_dovo_git_timeout_seconds(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        override: str | None,
        timeout: float,
        named: str,
    ) -> None:
        """Pass DOVO_GIT_TIMEOUT_SECONDS, or 120 when it is unset or not a positive number up to one day, to subprocess.run and name it in the GIT_TIMEOUT message."""
        timeouts: list[object] = []

        def _time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            timeouts.append(kwargs["timeout"])
            raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=1)

        if override is None:
            monkeypatch.delenv("DOVO_GIT_TIMEOUT_SECONDS", raising=False)
        else:
            monkeypatch.setenv("DOVO_GIT_TIMEOUT_SECONDS", override)
        monkeypatch.setattr("dovo.core.git.runner.subprocess.run", _time_out)

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec(["status"], git_repo)

        assert timeouts == [timeout]
        assert excinfo.value.message == f"Git timed out after {named} ('git status')"

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param(ErrorCode.GIT_TIMEOUT, id="timeout"),
            pytest.param(ErrorCode.GIT_WORKDIR_MISSING, id="workdir_missing"),
        ],
    )
    def test_status_porcelain_propagates_codes_it_does_not_swallow(self, tmp_path: Path, code: ErrorCode) -> None:
        """Raise the GitError(code) git status raised instead of returning []."""
        failure = GitError(code, "boom")
        fake = FakeGitRunner({("status",): failure})

        with pytest.raises(GitError) as excinfo:
            fake.status_porcelain(tmp_path)

        assert excinfo.value is failure

    def test_apply_check_returns_no_conflicts_when_the_check_passes(self, tmp_path: Path) -> None:
        """Return [] and record calls == [("apply", "--check", "--binary", "-")] when the check exits 0."""
        fake = FakeGitRunner({("apply", "--check"): GitProcessOutput(0, "", "")})

        assert fake.apply_check(tmp_path, "diff --git a/a.txt b/a.txt") == []
        assert fake.calls == [("apply", "--check", "--binary", "-")]

    def test_apply_check_returns_the_conflicting_paths_it_names(self, tmp_path: Path) -> None:
        """Return ["a.txt"] when the check exits 1 with stderr "error: patch failed: a.txt:1"."""
        fake = FakeGitRunner({("apply", "--check"): GitProcessOutput(1, "", "error: patch failed: a.txt:1")})

        assert fake.apply_check(tmp_path, "diff --git a/a.txt b/a.txt") == ["a.txt"]

    @pytest.mark.parametrize(
        ("output", "message"),
        [
            pytest.param(
                GitProcessOutput(128, "", "error: No valid patches in input\n"),
                "error: No valid patches in input",
                id="stderr",
            ),
            pytest.param(GitProcessOutput(1, "", ""), "patch check failed", id="empty_stderr"),
        ],
    )
    def test_apply_check_raises_git_failed_when_it_fails_naming_no_path(
        self, tmp_path: Path, output: GitProcessOutput, message: str
    ) -> None:
        """Raise GitError GIT_FAILED with the check's stripped stderr, else "patch check failed"."""
        fake = FakeGitRunner({("apply", "--check"): output})

        with pytest.raises(GitError) as excinfo:
            fake.apply_check(tmp_path, "diff --git a/a.txt b/a.txt")

        assert excinfo.value.error_code is ErrorCode.GIT_FAILED
        assert excinfo.value.message == message

    @pytest.mark.parametrize(
        ("stderr", "message"),
        [
            pytest.param("error: boom\n", "error: boom", id="stderr"),
            pytest.param("", "patch failed", id="empty_stderr"),
        ],
    )
    def test_apply_raises_git_failed_on_nonzero_exit(self, tmp_path: Path, stderr: str, message: str) -> None:
        """Raise GitError GIT_FAILED with git apply's stripped stderr, else "patch failed", recording the apply call."""
        fake = FakeGitRunner({("apply", "--binary"): GitProcessOutput(1, "", stderr)})

        with pytest.raises(GitError) as excinfo:
            fake.apply(tmp_path, "diff --git a/a.txt b/a.txt", index=True)

        assert excinfo.value.error_code is ErrorCode.GIT_FAILED
        assert excinfo.value.message == message
        assert fake.calls == [("apply", "--binary", "--index", "-")]


class GitRunnerBytesTests:
    def test_run_bytes_feeds_stdin_and_returns_stdout_byte_exact(self, git_repo: Path) -> None:
        """Store non-UTF-8 stdin bytes with hash-object -w, keeping its trailing newline, and read them back byte-exact."""
        sha = GitRunner().run_bytes(["hash-object", "-w", "--stdin"], git_repo, input_bytes=b"\xff\x00raw")

        assert sha.endswith(b"\n")
        assert GitRunner().run_bytes(["cat-file", "blob", sha.decode().strip()], git_repo) == b"\xff\x00raw"

    def test_exec_bytes_returns_nul_separated_output_for_any_exit_code(self, git_repo: Path) -> None:
        """Return the -z NUL-separated listing for exit 0 and exit 1 without raising when nothing matches."""
        (git_repo / "a b.txt").write_text("a\n", encoding="utf-8")

        listed = GitRunner().exec_bytes(["ls-files", "-z", "--others"], git_repo)
        absent = GitRunner().exec_bytes(["show-ref", "--verify", "--quiet", "refs/heads/absent"], git_repo)

        assert listed == GitBytesOutput(returncode=0, stdout=b"a b.txt\0", stderr=b"")
        assert absent == GitBytesOutput(returncode=1, stdout=b"", stderr=b"")

    def test_run_bytes_raises_git_failed_naming_the_decoded_stderr(self, git_repo: Path) -> None:
        """Raise GitError GIT_FAILED "Git execution failed ('git rev-parse --verify nope'): fatal: Needed a single revision"."""
        with pytest.raises(GitError) as excinfo:
            GitRunner().run_bytes(["rev-parse", "--verify", "nope"], git_repo)

        assert excinfo.value.error_code is ErrorCode.GIT_FAILED
        assert excinfo.value.message == (
            "Git execution failed ('git rev-parse --verify nope'): fatal: Needed a single revision"
        )

    def test_exec_bytes_raises_git_timeout(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raise GitError GIT_TIMEOUT whose message ends "('git status')" when subprocess.run times out."""

        def _time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=1)

        monkeypatch.setattr("dovo.core.git.runner.subprocess.run", _time_out)

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec_bytes(["status"], git_repo)

        assert str(excinfo.value).endswith("('git status')")
        assert excinfo.value.error_code is ErrorCode.GIT_TIMEOUT

    def test_exec_bytes_raises_git_binary_missing_unchained(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Raise GitError GIT_BINARY_MISSING "Git execution failed ('git status'): git not found" with no cause when PATH holds no git."""
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec_bytes(["status"], git_repo)

        assert excinfo.value.error_code is ErrorCode.GIT_BINARY_MISSING
        assert excinfo.value.message == "Git execution failed ('git status'): git not found"
        assert excinfo.value.__cause__ is None

    def test_exec_bytes_raises_git_workdir_missing_chained(self, tmp_path: Path) -> None:
        """Raise GitError GIT_WORKDIR_MISSING naming the directory, chained to the OSError, when path does not exist."""
        missing = tmp_path / "gone"

        with pytest.raises(GitError) as excinfo:
            GitRunner().exec_bytes(["status"], missing)

        assert excinfo.value.error_code is ErrorCode.GIT_WORKDIR_MISSING
        assert excinfo.value.message == (
            f"Git execution failed ('git status'): working directory '{missing}' does not exist"
        )
        assert isinstance(excinfo.value.__cause__, FileNotFoundError)


class ExtractConflictsTests:
    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            pytest.param("error: patch failed: src/main.py:12\n", ["src/main.py"], id="patch_failed"),
            pytest.param("error: src/main.py: patch does not apply\n", ["src/main.py"], id="does_not_apply"),
            pytest.param("cannot apply binary patch to 'assets/logo.png'\n", ["assets/logo.png"], id="binary_patch"),
            pytest.param(
                "error: src/new.py: already exists in working directory\n", ["src/new.py"], id="already_exists"
            ),
            pytest.param("error: src/gone.py: does not exist in index\n", ["src/gone.py"], id="not_in_index"),
            pytest.param("error: src/gone.py: No such file or directory\n", ["src/gone.py"], id="missing_in_workspace"),
            pytest.param("Applying patch\nnothing to see here\n", [], id="no_conflict_markers"),
            pytest.param("", [], id="empty_stderr"),
            pytest.param(
                "error: patch failed: a.py:1\nerror: patch failed: b.py:5\nerror: a.py: patch does not apply",
                ["a.py", "b.py"],
                id="deduplicated_and_sorted",
            ),
        ],
    )
    def test_extracts_conflicting_paths_from_known_git_apply_stderr_patterns(
        self, stderr: str, expected: list[str]
    ) -> None:
        assert extract_conflicts(stderr) == expected
