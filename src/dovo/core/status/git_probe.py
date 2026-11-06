"""Git repository probe for workspace status."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from dovo.common.diagnostic_log import DiagnosticLevel, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.core.git import GitError, GitRunner
from dovo.core.status.models import GitStatusInfo

_UNREADABLE_CODES: Final = frozenset({ErrorCode.GIT_BINARY_MISSING, ErrorCode.GIT_TIMEOUT})


def _not_a_repo() -> GitStatusInfo:
    """Return the info for a directory git says is not a work tree."""
    return GitStatusInfo(is_git_repo=False, branch="none", is_dirty=False, uncommitted_files=0)


def _read_git(root_dir: Path, git: GitRunner) -> GitStatusInfo:
    """Return the branch, dirty state and uncommitted file count of root_dir, or the not-a-repo info."""
    is_inside = git.run(["rev-parse", "--is-inside-work-tree"], path=root_dir).strip()
    if is_inside != "true":
        return _not_a_repo()
    branch = git.get_current_branch(root_dir)
    status_lines = git.status_porcelain(root_dir)
    return GitStatusInfo(
        is_git_repo=True,
        branch=branch,
        is_dirty=bool(status_lines),
        uncommitted_files=len(status_lines),
    )


def probe_git(root_dir: Path, *, git: GitRunner) -> GitStatusInfo:
    """Return the branch, dirty state and uncommitted file count of root_dir, or why git could not read it.

    A non-zero git exit gives the not-a-repo info. A git timeout or a missing git binary is recorded at error level
    and gives an info with branch "unknown", the GitError's code, its message as the warning and the record.

    Raises:
        GitError: With GIT_WORKDIR_MISSING when root_dir is not a directory.
    """
    try:
        return _read_git(root_dir, git)
    except GitError as exc:
        if exc.error_code is ErrorCode.GIT_FAILED:
            return _not_a_repo()
        if exc.error_code not in _UNREADABLE_CODES:
            raise
        record = record_exception(
            exc, level=DiagnosticLevel.ERROR, operation="status.git", context={"path": str(root_dir)}
        )
        return GitStatusInfo(
            is_git_repo=False,
            branch="unknown",
            is_dirty=False,
            uncommitted_files=0,
            error_code=exc.error_code,
            warning=exc.message,
            diagnostic=record,
        )
