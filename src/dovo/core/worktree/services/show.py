"""Worktree detail inspection service."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import (
    WorktreesRepository,
    WorktreeStatus,
)
from dovo.core.worktree.models import (
    WorktreeShowResult,
    WorktreeShowStatus,
)


def collect_worktree_show(
    paths: WorkspacePaths,
    db: WorktreesRepository,
    worktree_id: str,
) -> WorktreeShowResult:
    """Look up one worktree, and reconcile a stale active row.

    Args:
        paths: Resolved command-invocation workspace paths.
        db: WorktreesRepository instance.
        worktree_id: Worktree primary key to show.

    Returns:
        Structured show result. Does not print or exit.
    """
    if paths.project_id is None:
        return WorktreeShowResult(
            status=WorktreeShowStatus.NOT_INITIALIZED,
            errors=["Workspace is not initialized."],
            fixes=["Run `dovo init` to initialize this workspace."],
        )

    row = db.get(worktree_id)
    if row is None:
        return WorktreeShowResult(
            status=WorktreeShowStatus.NOT_FOUND,
            errors=[f"Worktree '{worktree_id}' not found."],
            fixes=["Run `dovo worktree list` to see known worktrees"],
        )

    reconciled = False
    if row.status is WorktreeStatus.ACTIVE and not Path(row.worktree_path).is_dir():
        updated = db.update_status(row.id, WorktreeStatus.CLEANED)
        if updated is not None:
            row = updated
        else:
            row = row.model_copy(update={"status": WorktreeStatus.CLEANED})
        reconciled = True

    disk_present = Path(row.worktree_path).exists()
    return WorktreeShowResult(
        status=WorktreeShowStatus.OK,
        worktree=row,
        disk_present=disk_present,
        reconciled=reconciled,
    )
