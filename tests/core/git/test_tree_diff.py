"""Contract tests for diffing a work tree through a temporary index."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dovo.core.git.exceptions import GitCommandError, GitPlumbingTimeoutError
from dovo.core.git.models import WorkTreeDiff
from dovo.core.git.runner import GitRunner
from dovo.core.git.tree_diff import diff_work_tree
from tests.harness.git_index import snapshot_index, temp_index_files

_BASE_FILES = {
    ".gitignore": "*.log\n",
    "staged.txt": "staged base\n",
    "unstaged.txt": "unstaged base\n",
    "deleted.txt": "deleted base\n",
}


def _linked_worktree(git_repo: Path, tmp_path: Path) -> tuple[Path, str]:
    """Commit the base files on main, add a linked worktree from it and return (worktree, base sha)."""
    for name, content in _BASE_FILES.items():
        (git_repo / name).write_text(content, encoding="utf-8")
    GitRunner.run(["add", "-A"], path=git_repo)
    GitRunner.run(["commit", "-m", "Add base files"], path=git_repo)
    base_commit = GitRunner.rev_parse(git_repo, rev="HEAD")
    worktree = tmp_path / "linked"
    GitRunner.worktree_add(git_repo, worktree, "feature", "main")
    return worktree, base_commit


def _make_every_change_kind(worktree: Path) -> None:
    """Leave committed, staged, unstaged, deleted, untracked and ignored changes in worktree."""
    (worktree / "committed.txt").write_text("committed\n", encoding="utf-8")
    GitRunner.run(["add", "committed.txt"], path=worktree)
    GitRunner.run(["commit", "-m", "Add committed.txt"], path=worktree)
    (worktree / "staged.txt").write_text("staged change\n", encoding="utf-8")
    GitRunner.run(["add", "staged.txt"], path=worktree)
    (worktree / "unstaged.txt").write_text("unstaged change\n", encoding="utf-8")
    (worktree / "deleted.txt").unlink()
    (worktree / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (worktree / "ignored.log").write_text("ignored\n", encoding="utf-8")


class DiffWorkTreeTests:
    def test_diff_covers_every_change_kind_and_excludes_ignored_files(self, git_repo: Path, tmp_path: Path) -> None:
        worktree, base_commit = _linked_worktree(git_repo, tmp_path)
        _make_every_change_kind(worktree)

        delta = diff_work_tree(worktree, base_commit)

        expected = ["committed.txt", "deleted.txt", "staged.txt", "unstaged.txt", "untracked.txt"]
        assert delta.files == expected
        for name in expected:
            assert f"diff --git a/{name} b/{name}" in delta.diff_text
            assert name in delta.stat_text
        assert "ignored.log" not in delta.diff_text
        assert "ignored.log" not in delta.stat_text

    def test_leaves_real_index_and_status_unchanged_and_removes_temp_index(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        worktree, base_commit = _linked_worktree(git_repo, tmp_path)
        _make_every_change_kind(worktree)
        before = snapshot_index(worktree)

        diff_work_tree(worktree, base_commit)

        after = snapshot_index(worktree)
        assert after == before
        assert "?? untracked.txt" in after.status.splitlines()
        assert temp_index_files(worktree) == []

    def test_clean_worktree_returns_empty_diff_and_leaves_no_temp_index(self, git_repo: Path, tmp_path: Path) -> None:
        worktree, base_commit = _linked_worktree(git_repo, tmp_path)

        delta = diff_work_tree(worktree, base_commit)

        assert delta == WorkTreeDiff()
        assert temp_index_files(worktree) == []

    def test_git_failure_raises_and_removes_temp_index(self, git_repo: Path, tmp_path: Path) -> None:
        worktree, _ = _linked_worktree(git_repo, tmp_path)
        (worktree / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        before = snapshot_index(worktree)

        with pytest.raises(GitCommandError):
            diff_work_tree(worktree, "0" * 40)

        assert snapshot_index(worktree) == before
        assert temp_index_files(worktree) == []

    def test_does_not_reuse_or_remove_another_computations_temp_index(self, git_repo: Path, tmp_path: Path) -> None:
        worktree, base_commit = _linked_worktree(git_repo, tmp_path)
        (worktree / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        git_dir = Path(GitRunner.run(["rev-parse", "--absolute-git-dir"], path=worktree))
        other = git_dir / "dovo-diff-index-other"
        other.write_bytes(b"another computation's index")

        delta = diff_work_tree(worktree, base_commit)

        assert delta.files == ["untracked.txt"]
        assert other.read_bytes() == b"another computation's index"
        assert temp_index_files(worktree) == ["dovo-diff-index-other"]

    def test_concurrent_computations_both_succeed_and_leave_no_temp_index(self, git_repo: Path, tmp_path: Path) -> None:
        worktree, base_commit = _linked_worktree(git_repo, tmp_path)
        _make_every_change_kind(worktree)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(lambda _: diff_work_tree(worktree, base_commit), range(2))

        assert first == second
        assert first.files == ["committed.txt", "deleted.txt", "staged.txt", "unstaged.txt", "untracked.txt"]
        assert temp_index_files(worktree) == []

    def test_relative_path_in_main_checkout_diffs_untracked_file(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base_commit = GitRunner.rev_parse(git_repo, rev="HEAD")
        (git_repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        monkeypatch.chdir(git_repo.parent)

        delta = diff_work_tree(Path(git_repo.name), base_commit)

        assert delta.files == ["untracked.txt"]
        assert temp_index_files(git_repo) == []

    def test_git_killed_mid_add_removes_temp_index_and_its_lock(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree, base_commit = _linked_worktree(git_repo, tmp_path)
        real_run = GitRunner.run

        def run_killed_on_add(args: list[str], path: Path, *, env: Mapping[str, str] | None = None) -> str:
            if args[0] == "add" and env is not None:
                Path(f"{env['GIT_INDEX_FILE']}.lock").write_bytes(b"")
                raise GitPlumbingTimeoutError("git add timed out")
            return real_run(args, path, env=env)

        monkeypatch.setattr(GitRunner, "run", staticmethod(run_killed_on_add))

        with pytest.raises(GitPlumbingTimeoutError):
            diff_work_tree(worktree, base_commit)

        assert temp_index_files(worktree) == []
