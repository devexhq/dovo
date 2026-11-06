"""Worktree inventory probe for workspace status."""

from __future__ import annotations

from typing import Final

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, record_exception
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.status.models import WorktreeStatusInfo

_FALLBACK_WARNING: Final = "Could not read worktrees from the database; counted worktree directories instead."


def probe_worktrees(paths: WorkspacePaths, *, max_active_worktrees: int, query_database: bool) -> WorktreeStatusInfo:
    """Count active and total worktrees from the database when query_database is set, else from worktree directories.

    When the database query raises, the failure is recorded at error level and the directories under
    paths.worktrees_dir are counted instead, with the fallback warning and the record.
    """
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
        except Exception as exc:
            record = record_exception(
                exc,
                level=DiagnosticLevel.ERROR,
                operation="status.worktrees",
                context={"path": str(paths.database_file)},
            )
            return _count_directories(paths, max_active_worktrees, warning=_FALLBACK_WARNING, diagnostic=record)

    return _count_directories(paths, max_active_worktrees)


def _count_directories(
    paths: WorkspacePaths,
    max_active_worktrees: int,
    *,
    warning: str | None = None,
    diagnostic: DiagnosticRecord | None = None,
) -> WorktreeStatusInfo:
    """Count the directories under paths.worktrees_dir, or 0 when it does not exist, as both active and total."""
    worktrees_dir = paths.worktrees_dir
    dir_count = len([path for path in worktrees_dir.iterdir() if path.is_dir()]) if worktrees_dir.is_dir() else 0
    return WorktreeStatusInfo(
        active_worktrees=dir_count,
        total_worktrees=dir_count,
        max_active_worktrees=max_active_worktrees,
        warning=warning,
        diagnostic=diagnostic,
    )
