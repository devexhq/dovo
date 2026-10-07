"""Collection service for worktree deletion."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.worktree.models import WorktreeDeleteResult, WorktreeDeleteStatus


def collect_worktree_delete(
    paths: WorkspacePaths,
    db: WorktreesRepository,
    *,
    worktree_id: str,
) -> WorktreeDeleteResult:
    """Look up one worktree for delete (no mutation)."""
    row = db.get(worktree_id)
    if row is None:
        return WorktreeDeleteResult(
            status=WorktreeDeleteStatus.NOT_FOUND,
            worktree_id=worktree_id,
            errors=[f"Worktree '{worktree_id}' not found."],
            fixes=["Run `dovo worktree list` to see known worktrees"],
        )

    if row.status is WorktreeStatus.CLEANED:
        return WorktreeDeleteResult(
            status=WorktreeDeleteStatus.ALREADY_CLEANED,
            worktree_id=worktree_id,
            worktree=row,
        )

    return WorktreeDeleteResult(
        status=WorktreeDeleteStatus.READY,
        worktree_id=worktree_id,
        worktree=row,
    )
