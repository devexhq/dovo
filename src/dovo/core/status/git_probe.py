"""Git repository probe for workspace status."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from dovo.common.error_codes import ErrorCode
from dovo.core.git import GitError, GitRunner
from dovo.core.status.models import GitStatusInfo

_BRANCH_UNKNOWN_CODES: Final = frozenset({ErrorCode.GIT_BINARY_MISSING, ErrorCode.GIT_TIMEOUT})


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
    except GitError as exc:
        if exc.error_code in _BRANCH_UNKNOWN_CODES:
            branch = "unknown"
        elif exc.error_code is ErrorCode.GIT_FAILED:
            branch = "none"
        else:
            raise
        return GitStatusInfo(
            is_git_repo=False,
            branch=branch,
            is_dirty=False,
            uncommitted_files=0,
        )
