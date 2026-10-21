"""Worktree inventory probe for workspace status."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.status.models import WorktreeStatusInfo


def probe_worktrees(paths: WorkspacePaths, *, max_active_worktrees: int, query_database: bool) -> WorktreeStatusInfo:
    """Count active and total worktrees from the database when query_database is set, else from worktree directories.

    Falls back to counting directories under paths.worktrees_dir when the database query raises.
    """
    worktrees_dir = paths.worktrees_dir

    if query_database:
        try:
            worktrees_repo = WorktreesRepository(
                db_path=paths.database_file, project_id=paths.project_id, auto_init=False
            )
            active_worktrees = len(worktrees_repo.list(status=WorktreeStatus.ACTIVE))
            total_worktrees = len(worktrees_repo.list())
            return WorktreeStatusInfo(
                active_worktrees=active_worktrees,
                total_worktrees=total_worktrees,
                max_active_worktrees=max_active_worktrees,
            )
        except Exception:
            pass

    if worktrees_dir.is_dir():
        dir_count = len([path for path in worktrees_dir.iterdir() if path.is_dir()])
        return WorktreeStatusInfo(
            active_worktrees=dir_count,
            total_worktrees=dir_count,
            max_active_worktrees=max_active_worktrees,
        )

    return WorktreeStatusInfo(
        active_worktrees=0,
        total_worktrees=0,
        max_active_worktrees=max_active_worktrees,
    )
