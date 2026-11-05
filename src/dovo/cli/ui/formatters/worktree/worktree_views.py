"""Presentation view models for worktree formatters."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.cli.ui.formatters.header import ResultHeaderView
from dovo.core.db import WorktreeRecord
from dovo.core.worktree.models import (
    PruneAction,
    StaleWorktreeCategory,
    WorktreeApplyStrategy,
    WorktreePruneStatus,
    WorktreeSession,
)


class PrunedItemView(BaseModel):
    """Semantic view of a single resource processed during prune."""

    model_config = {"extra": "forbid", "strict": True}

    category: StaleWorktreeCategory
    category_label: str
    identifier: str
    action: PruneAction
    is_dry_run: bool
    path: Path | None = None
    branch_name: str | None = None
    session_id: str | None = None
    reason: str = ""
    error: str | None = None


class WorktreePruneView(ResultHeaderView):
    """Semantic view of worktree pruning execution."""

    status: WorktreePruneStatus | None
    dry_run: bool
    force: bool
    items: list[PrunedItemView] = Field(default_factory=list)
    pruned_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0


class WorktreeListView(ResultHeaderView):
    """Semantic view of `dovo worktree list`: the header, then every worktree record."""

    worktrees: list[WorktreeRecord] = Field(default_factory=list)


class WorktreeShowView(ResultHeaderView):
    """Semantic view of `dovo worktree show`: the header, then the worktree and its disk state."""

    worktree: WorktreeRecord | None = None
    disk_present: bool = False
    reconciled: bool = False


class WorktreeCreateView(ResultHeaderView):
    """Semantic view of `dovo worktree create`: the header, then the created worktree session."""

    session: WorktreeSession | None = None


class WorktreeApplyView(ResultHeaderView):
    """Semantic view of `dovo worktree apply`: the header, then what the apply changed."""

    worktree_id: str
    strategy: WorktreeApplyStrategy = WorktreeApplyStrategy.PATCH
    touched_files: list[str] = Field(default_factory=list)
    conflicting_files: list[str] = Field(default_factory=list)
    cleaned_up: bool = False
    commit_sha: str | None = None


class WorktreeDiffView(ResultHeaderView):
    """Semantic view of `dovo worktree diff`: the header, then the diff, its stat and the changed files."""

    worktree_id: str
    diff_text: str = ""
    stat_text: str = ""
    files_changed: list[str] = Field(default_factory=list)


class WorktreeDeleteView(ResultHeaderView):
    """Semantic view of `dovo worktree delete`: the header, then what the delete did."""

    worktree_id: str = ""
    worktree: WorktreeRecord | None = None
    deleted: bool = False
    already_cleaned: bool = False
    cancelled: bool = False
