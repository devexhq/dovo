from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import catch_failure
from dovo.core.agents.mutation_git import (
    capture_binary_patch_since,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)
from dovo.core.git import GitError, GitProcessOutput, GitRunner
from tests.harness.diagnostic_log import read_single_event
from tests.harness.fakes import FakeGitRunner

DIRTY_KINDS = ["modified", "staged", "deleted", "untracked", "ignored_force_added"]


def _git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return its stripped stdout."""
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True).stdout.strip()


def _commit_file(repo: Path, name: str, content: str) -> None:
    """Write name with content and commit it."""
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")


def _make_dirty(repo: Path, kind: str) -> None:
    """Leave one kind of uncommitted change in repo, committing any setup it needs first."""
    if kind == "modified":
        (repo / "README.md").write_text("wip edit\n", encoding="utf-8")
    elif kind == "staged":
        (repo / "README.md").write_text("staged wip edit\n", encoding="utf-8")
        _git(repo, "add", "README.md")
    elif kind == "deleted":
        _commit_file(repo, "doomed.txt", "to be deleted\n")
        (repo / "doomed.txt").unlink()
    elif kind == "untracked":
        (repo / "wip.py").write_text("print('wip')\n", encoding="utf-8")
    elif kind == "ignored_force_added":
        _commit_file(repo, ".gitignore", "*.local\n")
        (repo / "notes.local").write_text("local notes\n", encoding="utf-8")
        _git(repo, "add", "-f", "notes.local")


def _worktree_files(repo: Path) -> dict[str, bytes]:
    """Return every file under repo outside .git, keyed by POSIX relative path."""
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


def _index_path(repo: Path) -> Path:
    """Return the checkout's index file as git resolves it, which for a linked worktree is outside repo."""
    index = Path(_git(repo, "rev-parse", "--git-path", "index"))
    return index if index.is_absolute() else repo / index


def _linked_worktree(repo: Path) -> Path:
    """Add a linked worktree of repo on a new branch and return its path."""
    linked = repo.parent / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "linked", str(linked))
    return linked


def _repository_state(repo: Path) -> tuple[str, str, str, bytes]:
    """Return HEAD, branch refs, porcelain status and the index file bytes."""
    return (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "for-each-ref", "refs/heads"),
        _git(repo, "--no-optional-locks", "status", "--porcelain"),
        _index_path(repo).read_bytes(),
    )


def _git_dir_listing(repo: Path) -> set[str]:
    """Return every path under .git except the object store."""
    git_dir = repo / ".git"
    return {
        path.relative_to(git_dir).as_posix()
        for path in git_dir.rglob("*")
        if path.relative_to(git_dir).parts[0] != "objects"
    }


def _agent_commits_readme_edit(repo: Path, content: str) -> None:
    """Edit README.md and commit every change, as an agent that commits its own work would."""
    (repo / "README.md").write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "agent commit")


class DirectMutationGitSafetyTests:
    """Integration tests verifying pre-agent WIP preservation during direct mutation rollbacks."""

    def test_mutation_git_preserves_wip_baseline_when_agent_discards_changes(
        self,
        git_repo: Path,
    ) -> None:
        readme_file = git_repo / "README.md"
        readme_file.write_text("# Test Repo\nUncommitted WIP edit in README\n", encoding="utf-8")

        wip_file = git_repo / "feature_wip.py"
        wip_file.write_text("print('uncommitted WIP code')\n", encoding="utf-8")

        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())

        readme_file.write_text("# Test Repo\nAgent corrupted README\n", encoding="utf-8")
        wip_file.write_text("print('agent modified this file')\n", encoding="utf-8")
        agent_junk = git_repo / "agent_garbage.tmp"
        agent_junk.write_text("agent temporary junk\n", encoding="utf-8")

        agent_diff, agent_touched = capture_diff_since(git_repo, baseline, git=GitRunner())
        assert agent_diff != ""
        assert agent_touched == ["README.md", "agent_garbage.tmp", "feature_wip.py"]

        discard_since(git_repo, baseline, git=GitRunner())

        assert readme_file.read_text(encoding="utf-8") == "# Test Repo\nUncommitted WIP edit in README\n"
        assert wip_file.read_text(encoding="utf-8") == "print('uncommitted WIP code')\n"
        assert agent_junk.exists() is False

        post_discard_diff, post_discard_touched = capture_diff_since(git_repo, baseline, git=GitRunner())
        assert post_discard_diff == ""
        assert post_discard_touched == []

    def test_kept_edit_then_read_only_step_leave_branch_history_unchanged(self, git_repo: Path) -> None:
        """Keep `git log --oneline` equal to its pre-baseline value and the first edit uncommitted after a kept edit and a second edit-free baseline on a dirty worktree."""
        _make_dirty(git_repo, "untracked")
        log_before = _git(git_repo, "log", "--oneline")

        first_baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        (git_repo / "README.md").write_text("kept agent edit\n", encoding="utf-8")
        _, first_touched = capture_diff_since(git_repo, first_baseline, git=GitRunner())
        second_baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        _, second_touched = capture_diff_since(git_repo, second_baseline, git=GitRunner())

        assert first_touched == ["README.md"]
        assert second_touched == []
        assert _git(git_repo, "log", "--oneline") == log_before
        assert _git(git_repo, "diff", "HEAD", "--name-only").splitlines() == ["README.md", "wip.py"]
        assert (git_repo / "README.md").read_text(encoding="utf-8") == "kept agent edit\n"


class ResolvePreAgentBaselineTests:
    def test_clean_tree_baselines_to_head(self, git_repo: Path) -> None:
        head = _git(git_repo, "rev-parse", "HEAD")
        refs_before = _git(git_repo, "for-each-ref", "refs/heads")
        commit_count_before = _git(git_repo, "rev-list", "--all", "--count")

        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())

        assert baseline == head
        assert _git(git_repo, "for-each-ref", "refs/heads") == refs_before
        assert _git(git_repo, "rev-list", "--all", "--count") == commit_count_before

    @pytest.mark.parametrize("linked", [False, True], ids=["main", "linked"])
    @pytest.mark.parametrize("kind", DIRTY_KINDS)
    def test_dirty_tree_returns_snapshot_of_worktree_on_head(self, git_repo: Path, kind: str, linked: bool) -> None:
        """Return a commit whose `%P` is the prior HEAD, whose `%B` stripped is `dovo: pre-agent baseline`, and whose `ls-tree -r --name-only` and `git show <sha>:<path>` contents equal the worktree, in a main checkout and a linked worktree."""
        repo = _linked_worktree(git_repo) if linked else git_repo
        _make_dirty(repo, kind)
        head = _git(repo, "rev-parse", "HEAD")
        worktree = _worktree_files(repo)

        baseline = resolve_pre_agent_baseline(repo, git=GitRunner())

        assert _git(repo, "show", "-s", "--format=%P", baseline) == head
        assert _git(repo, "show", "-s", "--format=%B", baseline) == "dovo: pre-agent baseline"
        assert sorted(_git(repo, "ls-tree", "-r", "--name-only", baseline).splitlines()) == sorted(worktree)
        snapshot = {
            name: subprocess.run(
                ["git", "show", f"{baseline}:{name}"], cwd=str(repo), capture_output=True, check=True
            ).stdout
            for name in worktree
        }
        assert snapshot == worktree

    @pytest.mark.parametrize("linked", [False, True], ids=["main", "linked"])
    @pytest.mark.parametrize("kind", DIRTY_KINDS)
    def test_dirty_tree_leaves_head_refs_status_and_index_unchanged(
        self, git_repo: Path, kind: str, linked: bool
    ) -> None:
        """Keep `rev-parse HEAD`, `for-each-ref refs/heads`, `--no-optional-locks status --porcelain` and the index file bytes identical across the call, in a main checkout and a linked worktree."""
        repo = _linked_worktree(git_repo) if linked else git_repo
        _make_dirty(repo, kind)
        before = _repository_state(repo)

        resolve_pre_agent_baseline(repo, git=GitRunner())

        assert _repository_state(repo) == before

    @pytest.mark.parametrize("subcommand", ["add", "write-tree", "commit-tree"])
    def test_failed_snapshot_raises_and_leaves_repository_unchanged(self, git_repo: Path, subcommand: str) -> None:
        """Raise GitError GIT_FAILED "Git execution failed ('git <subcommand> …'): injected" with HEAD, refs, index bytes, status, the worktree listing and the `.git` listing outside `objects/` unchanged when that git subcommand exits non-zero."""
        _make_dirty(git_repo, "untracked")
        state_before = _repository_state(git_repo)
        worktree_before = _worktree_files(git_repo)
        git_dir_before = _git_dir_listing(git_repo)
        fake = FakeGitRunner({(subcommand,): GitProcessOutput(1, "", "injected")})

        with pytest.raises(GitError) as raised:
            resolve_pre_agent_baseline(git_repo, git=fake)

        assert raised.value.error_code == ErrorCode.GIT_FAILED
        assert raised.value.message.startswith(f"Git execution failed ('git {subcommand}")
        assert raised.value.message.endswith("'): injected")
        assert _repository_state(git_repo) == state_before
        assert _worktree_files(git_repo) == worktree_before
        assert _git_dir_listing(git_repo) == git_dir_before

    def test_raises_git_failed_outside_a_repository(self, tmp_path: Path) -> None:
        """Raise GitError GIT_FAILED when the worktree path is not a git repository."""
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        with pytest.raises(GitError) as raised:
            resolve_pre_agent_baseline(not_a_repo, git=GitRunner())

        assert raised.value.error_code == ErrorCode.GIT_FAILED

    def test_raises_git_timeout_when_git_times_out(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raise GitError GIT_TIMEOUT when the git package's subprocess.run times out."""

        def _timeout(*_args: object, **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

        monkeypatch.setattr("dovo.core.git.runner.subprocess.run", _timeout)

        with pytest.raises(GitError) as raised:
            resolve_pre_agent_baseline(git_repo, git=GitRunner())

        assert raised.value.error_code == ErrorCode.GIT_TIMEOUT

    def test_missing_git_caught_with_catch_failure_leaves_no_event_file(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Copy GIT_BINARY_MISSING into caught.failure with no diagnostic when PATH holds no git, and create no logs directory."""
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))

        with catch_failure("agent.attempt") as caught:
            resolve_pre_agent_baseline(git_repo, git=GitRunner())

        assert caught.failure is not None
        assert caught.failure.error_code == ErrorCode.GIT_BINARY_MISSING
        assert caught.failure.diagnostic is None
        assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_missing_worktree_caught_with_catch_failure_records_the_cause(
        self, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Copy GIT_WORKDIR_MISSING into caught.failure and record the OSError when the worktree directory does not exist."""
        with catch_failure("agent.attempt") as caught:
            resolve_pre_agent_baseline(tmp_path / "gone", git=GitRunner())

        assert caught.failure is not None
        assert caught.failure.error_code == ErrorCode.GIT_WORKDIR_MISSING
        assert caught.failure.diagnostic is not None
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "agent.attempt"


class CaptureDiffSinceTests:
    def test_captures_modified_and_new_files(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        (git_repo / "README.md").write_text("agent edit\n", encoding="utf-8")
        (git_repo / "new_file.py").write_text("new file\n", encoding="utf-8")

        diff, touched = capture_diff_since(git_repo, baseline, git=GitRunner())

        assert touched == ["README.md", "new_file.py"]
        assert "agent edit" in diff
        assert "new file" in diff

    @pytest.mark.parametrize("dirty", [False, True])
    def test_captures_only_post_baseline_changes_including_agent_commits(self, git_repo: Path, dirty: bool) -> None:
        """Return touched == ["README.md"] and a diff containing the committed agent edit but not the pre-baseline `wip.py`."""
        if dirty:
            _make_dirty(git_repo, "untracked")
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        _agent_commits_readme_edit(git_repo, "committed by agent\n")

        diff, touched = capture_diff_since(git_repo, baseline, git=GitRunner())

        assert touched == ["README.md"]
        assert "committed by agent" in diff
        assert "wip.py" not in diff

    def test_empty_diff_when_no_changes(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())

        diff, touched = capture_diff_since(git_repo, baseline, git=GitRunner())

        assert diff == ""
        assert touched == []


class DiscardSinceTests:
    def test_discards_agent_edits_on_clean_baseline(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        (git_repo / "README.md").write_text("bad agent edit\n", encoding="utf-8")
        (git_repo / "junk.txt").write_text("untracked\n", encoding="utf-8")

        discard_since(git_repo, baseline, git=GitRunner())

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
        assert not (git_repo / "junk.txt").exists()

    def test_discard_preserves_wip_baseline(self, git_repo: Path) -> None:
        (git_repo / "README.md").write_text("wip content\n", encoding="utf-8")
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        (git_repo / "README.md").write_text("agent overwrote wip\n", encoding="utf-8")

        discard_since(git_repo, baseline, git=GitRunner())

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "wip content\n"

    @pytest.mark.parametrize("dirty", [False, True])
    def test_discard_restores_files_head_and_branch_to_baseline_time(self, git_repo: Path, dirty: bool) -> None:
        """Restore README.md and (dirty) untracked `wip.py` and force-added ignored `notes.local` to baseline content, remove the agent's new file, set HEAD and refs/heads/main to their pre-baseline SHA and leave `git status` as it was before the baseline after an agent edit, new file and commit."""
        if dirty:
            _make_dirty(git_repo, "ignored_force_added")
            _make_dirty(git_repo, "untracked")
        head_before = _git(git_repo, "rev-parse", "HEAD")
        files_before = _worktree_files(git_repo)
        status_before = _git(git_repo, "--no-optional-locks", "status", "--porcelain")

        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        if dirty:
            (git_repo / "wip.py").write_text("print('agent rewrote wip')\n", encoding="utf-8")
            (git_repo / "notes.local").write_text("agent notes\n", encoding="utf-8")
        (git_repo / "agent_new.py").write_text("print('agent')\n", encoding="utf-8")
        _agent_commits_readme_edit(git_repo, "agent edit\n")

        discard_since(git_repo, baseline, git=GitRunner())

        assert _worktree_files(git_repo) == files_before
        assert _git(git_repo, "rev-parse", "HEAD") == head_before
        assert _git(git_repo, "rev-parse", "refs/heads/main") == head_before
        assert _git(git_repo, "--no-optional-locks", "status", "--porcelain") == status_before

    @pytest.mark.parametrize("wip", ["force_added", "renamed"])
    def test_force_added_ignored_wip_stays_tracked_after_discard(self, git_repo: Path, wip: str) -> None:
        """Capture an agent edit to an ignored WIP file (force-added, or a tracked file renamed to an ignored name) under a baseline taken after an earlier discard."""
        if wip == "force_added":
            _make_dirty(git_repo, "ignored_force_added")
            ignored_path = git_repo / "notes.local"
        else:
            _commit_file(git_repo, ".gitignore", "*.local\n")
            _git(git_repo, "mv", "README.md", "moved.local")
            ignored_path = git_repo / "moved.local"
        first_baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        ignored_path.write_text("rejected agent notes\n", encoding="utf-8")
        discard_since(git_repo, first_baseline, git=GitRunner())

        second_baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        ignored_path.write_text("kept agent notes\n", encoding="utf-8")
        diff, touched = capture_diff_since(git_repo, second_baseline, git=GitRunner())

        assert touched == [ignored_path.name]
        assert "kept agent notes" in diff

    def test_unknown_baseline_raises_git_failed(self, git_repo: Path) -> None:
        """Raise GitError GIT_FAILED naming `git rev-parse --verify not-a-real-ref^{commit}` for an unknown baseline."""
        with pytest.raises(GitError) as raised:
            discard_since(git_repo, "not-a-real-ref", git=GitRunner())

        assert raised.value.error_code == ErrorCode.GIT_FAILED
        assert raised.value.message.startswith(
            "Git execution failed ('git rev-parse --verify not-a-real-ref^{commit}'):"
        )

    def test_merge_base_fatal_exit_raises_git_failed(self, git_repo: Path) -> None:
        """Raise GitError GIT_FAILED naming the merge-base command and its stderr when `merge-base --is-ancestor` exits 128."""
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        fake = FakeGitRunner({("merge-base",): GitProcessOutput(128, "", "fatal: bad object")})

        with pytest.raises(GitError) as raised:
            discard_since(git_repo, baseline, git=fake)

        assert raised.value.error_code == ErrorCode.GIT_FAILED
        assert raised.value.message == (
            f"Git execution failed ('git merge-base --is-ancestor {baseline} HEAD'): fatal: bad object"
        )

    def test_check_ignore_fatal_exit_raises_git_failed(self, git_repo: Path) -> None:
        """Raise GitError GIT_FAILED naming check-ignore and its stderr when it exits 128 while restaging ignored WIP."""
        _make_dirty(git_repo, "ignored_force_added")
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        fake = FakeGitRunner({("check-ignore",): GitProcessOutput(128, "", "fatal: bad")})

        with pytest.raises(GitError) as raised:
            discard_since(git_repo, baseline, git=fake)

        assert raised.value.error_code == ErrorCode.GIT_FAILED
        assert raised.value.message == "Git execution failed ('git check-ignore --no-index -z --stdin'): fatal: bad"


class CaptureBinaryPatchSinceTests:
    def test_tracked_untracked_and_binary_changes_yield_patch_that_applies_to_baseline(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        expected = {
            "README.md": b"# Test Repo\nagent edit\r\n",
            "new.txt": b"fresh\xff\n",
            "image.png": bytes(range(256)),
        }
        for name, content in expected.items():
            (git_repo / name).write_bytes(content)

        patch = capture_binary_patch_since(git_repo, baseline, git=GitRunner())
        discard_since(git_repo, baseline, git=GitRunner())
        subprocess.run(["git", "apply"], cwd=git_repo, input=patch, check=True)

        assert {name: (git_repo / name).read_bytes() for name in expected} == expected

    @pytest.mark.parametrize("dirty", [False, True])
    def test_patch_contains_only_post_baseline_changes_including_agent_commits(
        self, git_repo: Path, dirty: bool
    ) -> None:
        """Return a patch naming README.md with the committed agent edit and never naming the pre-baseline `wip.py`."""
        if dirty:
            _make_dirty(git_repo, "untracked")
        baseline = resolve_pre_agent_baseline(git_repo, git=GitRunner())
        _agent_commits_readme_edit(git_repo, "agent edit\n")

        patch = capture_binary_patch_since(git_repo, baseline, git=GitRunner())

        assert b"README.md" in patch
        assert b"agent edit" in patch
        assert b"wip.py" not in patch

    def test_unknown_baseline_raises_git_failed(self, git_repo: Path) -> None:
        """Raise GitError GIT_FAILED for a baseline git cannot resolve."""
        with pytest.raises(GitError) as raised:
            capture_binary_patch_since(git_repo, "not-a-real-ref", git=GitRunner())

        assert raised.value.error_code == ErrorCode.GIT_FAILED


def _resolve(repo: Path, baseline: str, git: GitRunner) -> object:
    """Take the pre-agent baseline of repo, ignoring baseline."""
    return resolve_pre_agent_baseline(repo, git=git)


def _capture_diff(repo: Path, baseline: str, git: GitRunner) -> object:
    """Capture the diff since baseline."""
    return capture_diff_since(repo, baseline, git=git)


def _capture_patch(repo: Path, baseline: str, git: GitRunner) -> object:
    """Capture the binary patch since baseline."""
    return capture_binary_patch_since(repo, baseline, git=git)


def _discard(repo: Path, baseline: str, git: GitRunner) -> object:
    """Discard the changes since baseline."""
    discard_since(repo, baseline, git=git)
    return None


_OPERATIONS: dict[str, tuple[tuple[str, ...], Callable[[Path, str, GitRunner], object]]] = {
    "baseline_clean": (("rev-parse", "HEAD"), _resolve),
    "baseline_dirty": (("commit-tree",), _resolve),
    "capture_diff": (("diff", "--cached"), _capture_diff),
    "capture_patch": (("diff", "--cached", "--binary"), _capture_patch),
    "discard": (("read-tree",), _discard),
}


def _prepare(repo: Path, operation: str) -> str:
    """Leave repo ready for operation and return its baseline: WIP for a dirty baseline, an agent edit after one otherwise."""
    if operation == "baseline_dirty":
        _make_dirty(repo, "untracked")
    if operation.startswith("baseline"):
        return "HEAD"
    baseline = resolve_pre_agent_baseline(repo, git=GitRunner())
    (repo / "README.md").write_text("agent edit\n", encoding="utf-8")
    return baseline


class MutationGitFailureCodeTests:
    @pytest.mark.parametrize("operation", list(_OPERATIONS))
    @pytest.mark.parametrize(
        ("response", "code"),
        [
            pytest.param(
                GitError(ErrorCode.GIT_TIMEOUT, "Git timed out after 120s ('git x')"),
                ErrorCode.GIT_TIMEOUT,
                id="timeout",
            ),
            pytest.param(GitProcessOutput(1, "", "injected"), ErrorCode.GIT_FAILED, id="nonzero_exit"),
            pytest.param(
                GitError(ErrorCode.GIT_BINARY_MISSING, "Git execution failed ('git x'): git not found"),
                ErrorCode.GIT_BINARY_MISSING,
                id="binary_missing",
            ),
        ],
    )
    def test_git_failure_raises_its_code_and_leaves_worktree_unchanged(
        self, git_repo: Path, operation: str, response: GitProcessOutput | GitError, code: ErrorCode
    ) -> None:
        """Raise GitError with code, and keep the worktree files, HEAD and branch refs as they were, when the operation's git command fails that way."""
        baseline = _prepare(git_repo, operation)
        key, run = _OPERATIONS[operation]
        files_before = _worktree_files(git_repo)
        head_before = _git(git_repo, "rev-parse", "HEAD")
        refs_before = _git(git_repo, "for-each-ref", "refs/heads")

        with pytest.raises(GitError) as raised:
            run(git_repo, baseline, FakeGitRunner({key: response}))

        assert raised.value.error_code == code
        assert _worktree_files(git_repo) == files_before
        assert _git(git_repo, "rev-parse", "HEAD") == head_before
        assert _git(git_repo, "for-each-ref", "refs/heads") == refs_before

    @pytest.mark.parametrize("operation", ["baseline_clean", "capture_diff", "capture_patch", "discard"])
    def test_vanished_worktree_raises_git_workdir_missing(self, tmp_path: Path, operation: str) -> None:
        """Raise GitError GIT_WORKDIR_MISSING chained to the OSError when the worktree directory no longer exists."""
        _, run = _OPERATIONS[operation]

        with pytest.raises(GitError) as raised:
            run(tmp_path / "gone", "HEAD", GitRunner())

        assert raised.value.error_code == ErrorCode.GIT_WORKDIR_MISSING
        assert isinstance(raised.value.__cause__, FileNotFoundError)
