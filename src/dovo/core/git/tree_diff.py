"""Diff a work tree against a base commit through a temporary index, leaving the real index untouched."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from dovo.core.git.models import WorkTreeDiff
from dovo.core.git.runner import GitRunner

TEMP_INDEX_PREFIX = "dovo-diff-index-"


def diff_work_tree(path: Path, base_commit: str, *, git: GitRunner) -> WorkTreeDiff:
    """Return the diff, touched files and diffstat of path's work tree against base_commit.

    Stages every non-ignored change into a uniquely named copy of the worktree's index inside
    its git directory, diffs that copy against base_commit, and deletes it (and any lock file
    git left beside it) before returning.

    Raises:
        OSError: When the worktree's index cannot be copied.
        GitNotFoundError: When git binary is not found.
        GitPlumbingTimeoutError: When a git command times out.
        GitCommandError: When a git command exits non-zero.
    """
    git_dir = _resolve_git_path(path, ["--git-dir"], git=git)
    real_index = _resolve_git_path(path, ["--git-path", "index"], git=git)
    fd, name = tempfile.mkstemp(prefix=TEMP_INDEX_PREFIX, dir=git_dir)
    os.close(fd)
    temp_index = Path(name)
    env = {"GIT_INDEX_FILE": str(temp_index)}
    try:
        shutil.copyfile(real_index, temp_index)
        git.run(["add", "-A"], path=path, env=env)
        diff_text = git.run(["diff", "--cached", "--binary", base_commit], path=path, env=env)
        names = git.run(["diff", "--cached", "--name-only", base_commit], path=path, env=env)
        stat_text = git.run(["diff", "--cached", "--stat", base_commit], path=path, env=env)
    finally:
        temp_index.unlink(missing_ok=True)
        temp_index.with_name(f"{temp_index.name}.lock").unlink(missing_ok=True)
    files = [line.strip() for line in names.splitlines() if line.strip()]
    return WorkTreeDiff(diff_text=diff_text, files=files, stat_text=stat_text)


def _resolve_git_path(path: Path, args: list[str], *, git: GitRunner) -> Path:
    """Run `git rev-parse <args>` in path and return its output as an absolute path."""
    resolved = Path(git.run(["rev-parse", *args], path=path))
    return resolved if resolved.is_absolute() else path.resolve() / resolved
