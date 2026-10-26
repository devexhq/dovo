"""Worktree detail inspection service."""

from __future__ import annotations

from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.db import (
    WorktreeRecord,
    WorktreesRepository,
    WorktreeStatus,
)
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.models import WorktreeShowResult


def find_worktree(db: WorktreesRepository, worktree_id: str) -> WorktreeRecord:
    """Return the worktree row for worktree_id.

    Raises:
        WorktreeError: WORKTREE_NOT_FOUND with "Worktree '<id>' not found." and the 'dovo worktree list' remediation.
    """
    row = db.get(worktree_id)
    if row is None:
        raise WorktreeError(
            ErrorCode.WORKTREE_NOT_FOUND,
            f"Worktree '{worktree_id}' not found.",
            [Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")],
        )
    return row


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

    Raises:
        WorktreeError: WORKTREE_NOT_FOUND when no row has worktree_id.
    """
    row = find_worktree(db, worktree_id)

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
        worktree=row,
        disk_present=disk_present,
        reconciled=reconciled,
    )
