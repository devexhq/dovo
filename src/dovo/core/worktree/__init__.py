"""Git worktree management and lifecycle."""

from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.models import (
    PruneAction,
    PrunedItem,
    StaleWorktreeCategory,
    StaleWorktreeItem,
    WorktreeApplyResult,
    WorktreeApplyStrategy,
    WorktreeCleanupResult,
    WorktreeCreateResult,
    WorktreeDeleteResult,
    WorktreeDetection,
    WorktreeDiffResult,
    WorktreeListResult,
    WorktreePruneResult,
    WorktreePruneStatus,
    WorktreeSession,
    WorktreeShowResult,
)
from dovo.core.worktree.worktree import Worktree

__all__ = [
    "PruneAction",
    "PrunedItem",
    "StaleWorktreeCategory",
    "StaleWorktreeItem",
    "Worktree",
    "WorktreeApplyResult",
    "WorktreeApplyStrategy",
    "WorktreeCleanupResult",
    "WorktreeCreateResult",
    "WorktreeDeleteResult",
    "WorktreeDetection",
    "WorktreeDiffResult",
    "WorktreeError",
    "WorktreeListResult",
    "WorktreePruneResult",
    "WorktreePruneStatus",
    "WorktreeSession",
    "WorktreeShowResult",
]
