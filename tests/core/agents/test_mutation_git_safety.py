from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import dovo.core.agents.mutation_git as mutation_mod
from dovo.core.agents.mutation_git import (
    MutationGitError,
    capture_binary_patch_since,
    capture_diff_since,
    discard_since,
    resolve_pre_agent_baseline,
)

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


def _repository_state(repo: Path) -> tuple[str, str, str, bytes]:
    """Return HEAD, branch refs, porcelain status and the index file bytes."""
    return (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "for-each-ref", "refs/heads"),
        _git(repo, "--no-optional-locks", "status", "--porcelain"),
        (repo / ".git" / "index").read_bytes(),
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

        baseline = resolve_pre_agent_baseline(git_repo)

        readme_file.write_text("# Test Repo\nAgent corrupted README\n", encoding="utf-8")
        wip_file.write_text("print('agent modified this file')\n", encoding="utf-8")
        agent_junk = git_repo / "agent_garbage.tmp"
        agent_junk.write_text("agent temporary junk\n", encoding="utf-8")

        agent_diff, agent_touched = capture_diff_since(git_repo, baseline)
        assert agent_diff != ""
        assert agent_touched == ["README.md", "agent_garbage.tmp", "feature_wip.py"]

        discard_since(git_repo, baseline)

        assert readme_file.read_text(encoding="utf-8") == "# Test Repo\nUncommitted WIP edit in README\n"
        assert wip_file.read_text(encoding="utf-8") == "print('uncommitted WIP code')\n"
        assert agent_junk.exists() is False

        post_discard_diff, post_discard_touched = capture_diff_since(git_repo, baseline)
        assert post_discard_diff == ""
        assert post_discard_touched == []

    def test_kept_edit_then_read_only_step_leave_branch_history_unchanged(self, git_repo: Path) -> None:
        """Keep `git log --oneline` equal to its pre-baseline value and the first edit uncommitted after a kept edit and a second edit-free baseline on a dirty worktree."""
        _make_dirty(git_repo, "untracked")
        log_before = _git(git_repo, "log", "--oneline")

        first_baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("kept agent edit\n", encoding="utf-8")
        _, first_touched = capture_diff_since(git_repo, first_baseline)
        second_baseline = resolve_pre_agent_baseline(git_repo)
        _, second_touched = capture_diff_since(git_repo, second_baseline)

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

        baseline = resolve_pre_agent_baseline(git_repo)

        assert baseline == head
        assert _git(git_repo, "for-each-ref", "refs/heads") == refs_before
        assert _git(git_repo, "rev-list", "--all", "--count") == commit_count_before

    @pytest.mark.parametrize("kind", DIRTY_KINDS)
    def test_dirty_tree_returns_snapshot_of_worktree_on_head(self, git_repo: Path, kind: str) -> None:
        """Return a commit whose `%P` is the prior HEAD, whose `%B` stripped is `dovo: pre-agent baseline`, and whose `ls-tree -r --name-only` and `git show <sha>:<path>` contents equal the worktree."""
        _make_dirty(git_repo, kind)
        head = _git(git_repo, "rev-parse", "HEAD")
        worktree = _worktree_files(git_repo)

        baseline = resolve_pre_agent_baseline(git_repo)

        assert _git(git_repo, "show", "-s", "--format=%P", baseline) == head
        assert _git(git_repo, "show", "-s", "--format=%B", baseline) == "dovo: pre-agent baseline"
        assert sorted(_git(git_repo, "ls-tree", "-r", "--name-only", baseline).splitlines()) == sorted(worktree)
        snapshot = {
            name: subprocess.run(
                ["git", "show", f"{baseline}:{name}"], cwd=str(git_repo), capture_output=True, check=True
            ).stdout
            for name in worktree
        }
        assert snapshot == worktree

    @pytest.mark.parametrize("kind", DIRTY_KINDS)
    def test_dirty_tree_leaves_head_refs_status_and_index_unchanged(self, git_repo: Path, kind: str) -> None:
        """Keep `rev-parse HEAD`, `for-each-ref refs/heads`, `--no-optional-locks status --porcelain` and the `.git/index` bytes identical across the call."""
        _make_dirty(git_repo, kind)
        before = _repository_state(git_repo)

        resolve_pre_agent_baseline(git_repo)

        assert _repository_state(git_repo) == before

    @pytest.mark.parametrize(
        ("subcommand", "action"),
        [
            ("add", "git add -A \\(pre-agent baseline\\)"),
            ("write-tree", "git write-tree \\(pre-agent baseline\\)"),
            ("commit-tree", "git commit-tree \\(pre-agent baseline\\)"),
        ],
    )
    def test_failed_snapshot_raises_and_leaves_repository_unchanged(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, subcommand: str, action: str
    ) -> None:
        """Raise MutationGitError matching `{action} failed` with HEAD, refs, index bytes, status, the worktree listing and the `.git` listing outside `objects/` unchanged when that git subcommand exits non-zero."""
        _make_dirty(git_repo, "untracked")
        state_before = _repository_state(git_repo)
        worktree_before = _worktree_files(git_repo)
        git_dir_before = _git_dir_listing(git_repo)
        real_run = subprocess.run

        def _fail_subcommand(
            args: list[str],
            *,
            cwd: str,
            env: dict[str, str] | None,
            capture_output: bool,
            check: bool,
            timeout: float,
        ) -> subprocess.CompletedProcess[bytes]:
            if args[1] == subcommand:
                return subprocess.CompletedProcess(args, 1, b"", b"injected")
            return real_run(args, cwd=cwd, env=env, capture_output=capture_output, check=check, timeout=timeout)

        monkeypatch.setattr(mutation_mod.subprocess, "run", _fail_subcommand)
        with pytest.raises(MutationGitError, match=f"{action} failed: injected"):
            resolve_pre_agent_baseline(git_repo)
        monkeypatch.undo()

        assert _repository_state(git_repo) == state_before
        assert _worktree_files(git_repo) == worktree_before
        assert _git_dir_listing(git_repo) == git_dir_before

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        with pytest.raises(MutationGitError):
            resolve_pre_agent_baseline(not_a_repo)

    def test_raises_on_git_timeout(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _timeout(*_args: object, **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=120)

        monkeypatch.setattr(mutation_mod.subprocess, "run", _timeout)

        with pytest.raises(MutationGitError, match="GIT_TIMEOUT"):
            resolve_pre_agent_baseline(git_repo)


class CaptureDiffSinceTests:
    def test_captures_modified_and_new_files(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("agent edit\n", encoding="utf-8")
        (git_repo / "new_file.py").write_text("new file\n", encoding="utf-8")

        diff, touched = capture_diff_since(git_repo, baseline)

        assert touched == ["README.md", "new_file.py"]
        assert "agent edit" in diff
        assert "new file" in diff

    @pytest.mark.parametrize("dirty", [False, True])
    def test_captures_only_post_baseline_changes_including_agent_commits(self, git_repo: Path, dirty: bool) -> None:
        """Return touched == ["README.md"] and a diff containing the committed agent edit but not the pre-baseline `wip.py`."""
        if dirty:
            _make_dirty(git_repo, "untracked")
        baseline = resolve_pre_agent_baseline(git_repo)
        _agent_commits_readme_edit(git_repo, "committed by agent\n")

        diff, touched = capture_diff_since(git_repo, baseline)

        assert touched == ["README.md"]
        assert "committed by agent" in diff
        assert "wip.py" not in diff

    def test_empty_diff_when_no_changes(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)

        diff, touched = capture_diff_since(git_repo, baseline)

        assert diff == ""
        assert touched == []


class DiscardSinceTests:
    def test_discards_agent_edits_on_clean_baseline(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("bad agent edit\n", encoding="utf-8")
        (git_repo / "junk.txt").write_text("untracked\n", encoding="utf-8")

        discard_since(git_repo, baseline)

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
        assert not (git_repo / "junk.txt").exists()

    def test_discard_preserves_wip_baseline(self, git_repo: Path) -> None:
        (git_repo / "README.md").write_text("wip content\n", encoding="utf-8")
        baseline = resolve_pre_agent_baseline(git_repo)
        (git_repo / "README.md").write_text("agent overwrote wip\n", encoding="utf-8")

        discard_since(git_repo, baseline)

        assert (git_repo / "README.md").read_text(encoding="utf-8") == "wip content\n"

    @pytest.mark.parametrize("dirty", [False, True])
    def test_discard_restores_files_head_and_branch_to_baseline_time(self, git_repo: Path, dirty: bool) -> None:
        """Restore README.md and (dirty) untracked `wip.py` and force-added ignored `notes.local` to baseline content, remove the agent's new file, and set HEAD and refs/heads/main to their pre-baseline SHA after an agent edit, new file and commit."""
        if dirty:
            _make_dirty(git_repo, "ignored_force_added")
            _make_dirty(git_repo, "untracked")
        head_before = _git(git_repo, "rev-parse", "HEAD")
        files_before = _worktree_files(git_repo)

        baseline = resolve_pre_agent_baseline(git_repo)
        if dirty:
            (git_repo / "wip.py").write_text("print('agent rewrote wip')\n", encoding="utf-8")
            (git_repo / "notes.local").write_text("agent notes\n", encoding="utf-8")
        (git_repo / "agent_new.py").write_text("print('agent')\n", encoding="utf-8")
        _agent_commits_readme_edit(git_repo, "agent edit\n")

        discard_since(git_repo, baseline)

        assert _worktree_files(git_repo) == files_before
        assert _git(git_repo, "rev-parse", "HEAD") == head_before
        assert _git(git_repo, "rev-parse", "refs/heads/main") == head_before

    def test_raises_on_git_failure(self, git_repo: Path) -> None:
        with pytest.raises(MutationGitError, match="git rev-parse baseline failed"):
            discard_since(git_repo, "not-a-real-ref")


class CaptureBinaryPatchSinceTests:
    def test_tracked_untracked_and_binary_changes_yield_patch_that_applies_to_baseline(self, git_repo: Path) -> None:
        baseline = resolve_pre_agent_baseline(git_repo)
        expected = {
            "README.md": b"# Test Repo\nagent edit\r\n",
            "new.txt": b"fresh\xff\n",
            "image.png": bytes(range(256)),
        }
        for name, content in expected.items():
            (git_repo / name).write_bytes(content)

        patch = capture_binary_patch_since(git_repo, baseline)
        discard_since(git_repo, baseline)
        subprocess.run(["git", "apply"], cwd=git_repo, input=patch, check=True)

        assert {name: (git_repo / name).read_bytes() for name in expected} == expected

    @pytest.mark.parametrize("dirty", [False, True])
    def test_patch_contains_only_post_baseline_changes_including_agent_commits(
        self, git_repo: Path, dirty: bool
    ) -> None:
        """Return a patch naming README.md with the committed agent edit and never naming the pre-baseline `wip.py`."""
        if dirty:
            _make_dirty(git_repo, "untracked")
        baseline = resolve_pre_agent_baseline(git_repo)
        _agent_commits_readme_edit(git_repo, "agent edit\n")

        patch = capture_binary_patch_since(git_repo, baseline)

        assert b"README.md" in patch
        assert b"agent edit" in patch
        assert b"wip.py" not in patch

    def test_unknown_baseline_raises_mutation_git_error(self, git_repo: Path) -> None:
        with pytest.raises(MutationGitError):
            capture_binary_patch_since(git_repo, "not-a-real-ref")
