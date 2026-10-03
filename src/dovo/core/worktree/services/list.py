"""Worktree listing service."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import (
    WorktreesRepository,
    WorktreeStatus,
)
from dovo.core.worktree.models import (
    WorktreeListResult,
    WorktreeListStatus,
)


def collect_worktree_list(
    paths: WorkspacePaths,
    db: WorktreesRepository,
    status: str | None = None,
) -> WorktreeListResult:
    """Reconcile stale active rows and return list data.

    Args:
        paths: Resolved command-invocation workspace paths.
        db: WorktreesRepository instance.
        status: Optional status filter (active, merged, cleaned,
            conflict). Reconciliation always runs on the full row set first.

    Returns:
        Structured list result. Does not print or exit.
    """
    if paths.project_id is None:
        return WorktreeListResult(status=WorktreeListStatus.NOT_INITIALIZED, worktrees=[])

    db.reconcile_stale_active()

    status_filter: WorktreeStatus | None = None
    if status is not None:
        status_filter = WorktreeStatus(status)

    rows = db.list(status=status_filter)
    return WorktreeListResult(status=WorktreeListStatus.OK, worktrees=rows)
