"""Presentation view models for worktree pruning formatters."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.core.worktree.models import (
    PruneAction,
    StaleWorktreeCategory,
    WorktreePruneStatus,
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


class WorktreePruneView(BaseModel):
    """Semantic view of worktree pruning execution."""

    model_config = {"extra": "forbid", "strict": True}

    status: WorktreePruneStatus
    dry_run: bool
    force: bool
    items: list[PrunedItemView] = Field(default_factory=list)
    pruned_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
