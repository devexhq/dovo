"""Git repository probe for workspace status."""

from __future__ import annotations

from pathlib import Path

from dovo.core.git import (
    GitCommandError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
    GitRunner,
)
from dovo.core.status.models import GitStatusInfo


def probe_git(root_dir: Path, *, git: GitRunner) -> GitStatusInfo:
    """Return the branch, dirty state and uncommitted file count of root_dir, or a not-a-repo info when git is unavailable."""
    try:
        is_inside = git.run(["rev-parse", "--is-inside-work-tree"], path=root_dir).strip()
        if is_inside != "true":
            return GitStatusInfo(
                is_git_repo=False,
                branch="none",
                is_dirty=False,
                uncommitted_files=0,
            )
        branch = git.get_current_branch(root_dir)
        status_lines = git.status_porcelain(root_dir)
        return GitStatusInfo(
            is_git_repo=True,
            branch=branch,
            is_dirty=bool(status_lines),
            uncommitted_files=len(status_lines),
        )
    except (GitNotFoundError, GitPlumbingTimeoutError):
        return GitStatusInfo(
            is_git_repo=False,
            branch="unknown",
            is_dirty=False,
            uncommitted_files=0,
        )
    except GitCommandError:
        return GitStatusInfo(
            is_git_repo=False,
            branch="none",
            is_dirty=False,
            uncommitted_files=0,
        )
