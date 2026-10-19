"""Snapshot a worktree's index bytes and status to prove an operation left them unchanged."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from dovo.core.git.runner import GitRunner
from dovo.core.git.tree_diff import TEMP_INDEX_PREFIX


class IndexSnapshot(BaseModel):
    """The worktree's index file bytes and porcelain status at one moment."""

    model_config = {"frozen": True}

    index: bytes
    status: str


def _git_path(worktree_path: Path, args: list[str]) -> Path:
    """Run `git rev-parse <args>` in worktree_path and return the result as an absolute path."""
    resolved = Path(GitRunner.run(["rev-parse", *args], path=worktree_path))
    return resolved if resolved.is_absolute() else worktree_path / resolved


def snapshot_index(worktree_path: Path) -> IndexSnapshot:
    """Read `git --no-optional-locks status --porcelain`, then the index bytes at `git rev-parse --git-path index`."""
    status = GitRunner.run(["--no-optional-locks", "status", "--porcelain"], path=worktree_path)
    index = _git_path(worktree_path, ["--git-path", "index"]).read_bytes()
    return IndexSnapshot(index=index, status=status)


def temp_index_files(worktree_path: Path) -> list[str]:
    """Return names of `dovo-diff-index-*` files in the worktree's git directory."""
    git_dir = _git_path(worktree_path, ["--git-dir"])
    return sorted(entry.name for entry in git_dir.iterdir() if entry.name.startswith(TEMP_INDEX_PREFIX))
