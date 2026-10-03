"""Pydantic and Enum models for Git worktrees."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult
from dovo.core.db import WorktreeRecord


class WorktreeListStatus(StrEnum):
    """Classified outcome for ``dovo worktree list``."""

    OK = "ok"
    NOT_INITIALIZED = "not_initialized"


class WorktreeListResult(BaseResult):
    """Structured result for ``dovo worktree list`` before rendering."""

    status: WorktreeListStatus
    worktrees: list[WorktreeRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when listing can proceed (including empty tables)."""
        return self.status == WorktreeListStatus.OK and not self.errors


class WorktreeShowStatus(StrEnum):
    """Classified outcome for ``dovo worktree show``."""

    OK = "ok"
    NOT_INITIALIZED = "not_initialized"
    NOT_FOUND = "not_found"


class WorktreeShowResult(BaseResult):
    """Structured result for ``dovo worktree show`` before rendering."""

    status: WorktreeShowStatus
    worktree: WorktreeRecord | None = None
    disk_present: bool = False
    reconciled: bool = False

    @property
    def ok(self) -> bool:
        """True when a worktree row is available to render."""
        return self.status == WorktreeShowStatus.OK and self.worktree is not None and not self.errors


class WorktreeSession(BaseModel):
    """Metadata for one isolated background git worktree."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    target_branch: str
    worktree_path: Path
    base_commit: str
    name: str | None = None
    created_at: str
    command_passed: bool | None = None
    wip_applied: bool = False
    wip_paths: list[str] = Field(default_factory=list)


class WorktreeCreateStatus(StrEnum):
    """Classified outcomes for creating a worktree."""

    OK = "ok"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    GIT_FAILED = "git_failed"
    GIT_TIMEOUT = "git_timeout"
    NOT_INITIALIZED = "not_initialized"
    STORAGE_BRIDGE_FAILED = "storage_bridge_failed"
    UNREADABLE_CONFIG = "unreadable_config"
    WIP_FAILED = "wip_failed"


class WorktreeCreateResult(BaseResult):
    """Non-raising result of worktree creation."""

    status: WorktreeCreateStatus
    session: WorktreeSession | None = None

    @property
    def ok(self) -> bool:
        """Return True when a worktree session was created successfully."""
        return self.status == WorktreeCreateStatus.OK and not self.errors


class WorktreeApplyStrategy(StrEnum):
    """Supported strategies for applying worktree changes to the main workspace."""

    PATCH = "patch"
    SQUASH = "squash"


class WorktreeApplyStatus(StrEnum):
    """Classified outcomes for applying a worktree."""

    OK = "ok"
    NOT_FOUND = "not_found"
    ALREADY_MERGED = "already_merged"
    MAIN_REPO_DIRTY = "main_repo_dirty"
    EMPTY_DIFF = "empty_diff"
    CONFLICT = "conflict"
    GIT_FAILED = "git_failed"


class WorktreeApplyResult(BaseResult):
    """Structured result of applying worktree changes."""

    status: WorktreeApplyStatus
    worktree_id: str
    strategy: WorktreeApplyStrategy = WorktreeApplyStrategy.PATCH
    touched_files: list[str] = Field(default_factory=list)
    conflicting_files: list[str] = Field(default_factory=list)
    cleaned_up: bool = False
    commit_sha: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when changes were applied successfully without errors."""
        return self.status == WorktreeApplyStatus.OK and not self.errors


class WorktreeDiffStatus(StrEnum):
    """Classified outcomes for inspecting worktree diffs."""

    OK = "ok"
    NOT_FOUND = "not_found"
    EMPTY_DIFF = "empty_diff"
    GIT_FAILED = "git_failed"


class WorktreeDiffResult(BaseResult):
    """Structured result of inspecting worktree diffs."""

    status: WorktreeDiffStatus
    worktree_id: str
    diff_text: str = ""
    stat_text: str = ""
    files_changed: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when diff was generated successfully without errors."""
        return self.status == WorktreeDiffStatus.OK and not self.errors


class StaleWorktreeCategory(StrEnum):
    """Classification category for stale worktree resources."""

    STALE_WORKTREE_REF = "stale_worktree_ref"
    ORPHANED_DIRECTORY = "orphaned_directory"
    STALE_DB_RECORD = "stale_db_record"
    STALE_BRANCH = "stale_branch"


class StaleWorktreeItem(BaseModel):
    """Detailed metadata for a detected stale or orphaned worktree resource."""

    model_config = {"extra": "forbid", "strict": True}

    category: StaleWorktreeCategory
    identifier: str
    path: Path | None = None
    branch_name: str | None = None
    session_id: str | None = None
    is_dirty: bool = False
    dirty_file_count: int = 0
    reason: str = ""


class WorktreeDetectionStatus(StrEnum):
    """Outcome status for stale worktree scanning."""

    OK = "ok"
    GIT_FAILED = "git_failed"
    UNREADABLE_CONFIG = "unreadable_config"
    ERROR = "error"


class WorktreeDetectionResult(BaseResult):
    """Structured, non-raising result of stale worktree detection."""

    status: WorktreeDetectionStatus = WorktreeDetectionStatus.OK
    items: list[StaleWorktreeItem] = Field(default_factory=list)
    active_worktree_count: int = 0

    @property
    def ok(self) -> bool:
        """Return True when detection completed without classified errors."""
        return self.status == WorktreeDetectionStatus.OK and not self.errors

    @property
    def total_stale_count(self) -> int:
        """Return total count of stale items detected."""
        return len(self.items)

    @property
    def has_stale_items(self) -> bool:
        """Return True if any stale items were detected."""
        return len(self.items) > 0

    @property
    def has_dirty_orphans(self) -> bool:
        """Return True if any orphaned directory has uncommitted changes."""
        return any(item.is_dirty for item in self.items)

    @property
    def stale_worktrees(self) -> list[StaleWorktreeItem]:
        """Return stale Git worktree registration items."""
        return [i for i in self.items if i.category == StaleWorktreeCategory.STALE_WORKTREE_REF]

    @property
    def orphaned_directories(self) -> list[StaleWorktreeItem]:
        """Return unindexed or non-active worktree directory items."""
        return [i for i in self.items if i.category == StaleWorktreeCategory.ORPHANED_DIRECTORY]

    @property
    def stale_db_records(self) -> list[StaleWorktreeItem]:
        """Return active database records missing on disk."""
        return [i for i in self.items if i.category == StaleWorktreeCategory.STALE_DB_RECORD]

    @property
    def stale_branches(self) -> list[StaleWorktreeItem]:
        """Return unlinked worktree branch items."""
        return [i for i in self.items if i.category == StaleWorktreeCategory.STALE_BRANCH]


class PruneAction(StrEnum):
    """Action taken on a detected stale resource during pruning."""

    PRUNED = "pruned"
    SKIPPED = "skipped"
    FAILED = "failed"


class WorktreePruneStatus(StrEnum):
    """Outcome status for worktree prune execution."""

    OK = "ok"
    PARTIAL_SUCCESS = "partial_success"
    GIT_FAILED = "git_failed"
    LOCKED = "locked"
    ERROR = "error"


class PrunedItem(BaseModel):
    """Details of a single resource processed during prune execution."""

    model_config = {"extra": "forbid", "strict": True}

    category: StaleWorktreeCategory
    identifier: str
    action: PruneAction
    path: Path | None = None
    branch_name: str | None = None
    session_id: str | None = None
    reason: str = ""
    error: str | None = None


class WorktreePruneResult(BaseResult):
    """Structured result of worktree pruning execution."""

    status: WorktreePruneStatus = WorktreePruneStatus.OK
    dry_run: bool = False
    force: bool = False
    items: list[PrunedItem] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True if prune completed cleanly without failures."""
        return self.status == WorktreePruneStatus.OK and not self.errors

    @property
    def pruned_items(self) -> list[PrunedItem]:
        """Return items that were successfully pruned."""
        return [i for i in self.items if i.action == PruneAction.PRUNED]

    @property
    def skipped_items(self) -> list[PrunedItem]:
        """Return items that were skipped (e.g. dirty orphans)."""
        return [i for i in self.items if i.action == PruneAction.SKIPPED]

    @property
    def failed_items(self) -> list[PrunedItem]:
        """Return items that failed during pruning."""
        return [i for i in self.items if i.action == PruneAction.FAILED]

    @property
    def pruned_count(self) -> int:
        """Total number of resources pruned."""
        return len(self.pruned_items)

    @property
    def skipped_count(self) -> int:
        """Total number of resources skipped."""
        return len(self.skipped_items)

    @property
    def failed_count(self) -> int:
        """Total number of resources that failed to prune."""
        return len(self.failed_items)


class WorktreeDeleteStatus(StrEnum):
    """Classified outcome for ``dovo worktree delete``."""

    READY = "ready"
    DELETED = "deleted"
    ALREADY_CLEANED = "already_cleaned"
    ABORTED = "aborted"
    NOT_INITIALIZED = "not_initialized"
    NOT_FOUND = "not_found"


class WorktreeDeleteResult(BaseResult):
    """Structured result for ``dovo worktree delete``."""

    status: WorktreeDeleteStatus
    worktree_id: str = ""
    worktree: WorktreeRecord | None = None
    deleted: bool = False

    @property
    def ok(self) -> bool:
        """True when delete succeeded (deleted), ready, or already-cleaned."""
        return (
            self.status
            in {
                WorktreeDeleteStatus.READY,
                WorktreeDeleteStatus.DELETED,
                WorktreeDeleteStatus.ALREADY_CLEANED,
            }
            and not self.errors
        )
