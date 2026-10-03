"""Worktree services for lifecycle, detection, pruning, list, show, delete, and patch operations."""

from dovo.core.worktree.services.delete import collect_worktree_delete
from dovo.core.worktree.services.detector import WorktreeDetector
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.list import collect_worktree_list
from dovo.core.worktree.services.patch import WorktreePatch
from dovo.core.worktree.services.pruner import WorktreePruner
from dovo.core.worktree.services.show import collect_worktree_show

__all__ = [
    "WorktreeDetector",
    "WorktreeLifecycle",
    "WorktreePatch",
    "WorktreePruner",
    "collect_worktree_delete",
    "collect_worktree_list",
    "collect_worktree_show",
]
