"""Pydantic and Enum models for Git worktrees."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.models import BaseResult
from dovo.core.db import WorktreeRecord


class WorktreeListResult(BaseResult):
    """Structured result for ``dovo worktree list`` before rendering."""

    worktrees: list[WorktreeRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None


class WorktreeShowResult(BaseResult):
    """Structured result for ``dovo worktree show`` before rendering."""

    worktree: WorktreeRecord | None = None
    disk_present: bool = False
    reconciled: bool = False

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None


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


class WorktreeCreateResult(BaseResult):
    """Result of worktree creation; a failure is reported by error_code."""

    session: WorktreeSession | None = None

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None


class WorktreeApplyStrategy(StrEnum):
    """Supported strategies for applying worktree changes to the main workspace."""

    PATCH = "patch"
    SQUASH = "squash"


class WorktreeApplyResult(BaseResult):
    """Structured result of applying worktree changes; a failure is reported by error_code."""

    worktree_id: str
    strategy: WorktreeApplyStrategy = WorktreeApplyStrategy.PATCH
    touched_files: list[str] = Field(default_factory=list)
    conflicting_files: list[str] = Field(default_factory=list)
    cleaned_up: bool = False
    commit_sha: str | None = None

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None


class WorktreeDiffResult(BaseResult):
    """Structured result of inspecting worktree diffs; a failure is reported by error_code."""

    worktree_id: str
    diff_text: str = ""
    stat_text: str = ""
    files_changed: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None


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


@dataclass(frozen=True, slots=True)
class WorktreeDetection:
    """Stale worktree resources found by one scan, with the scan's warnings and error-level records."""

    items: tuple[StaleWorktreeItem, ...]
    active_worktree_count: int
    warnings: tuple[str, ...]
    diagnostics: tuple[DiagnosticRecord, ...]

    @property
    def stale_worktrees(self) -> tuple[StaleWorktreeItem, ...]:
        """Return stale Git worktree registration items."""
        return tuple(i for i in self.items if i.category == StaleWorktreeCategory.STALE_WORKTREE_REF)

    @property
    def orphaned_directories(self) -> tuple[StaleWorktreeItem, ...]:
        """Return unindexed or non-active worktree directory items."""
        return tuple(i for i in self.items if i.category == StaleWorktreeCategory.ORPHANED_DIRECTORY)

    @property
    def stale_db_records(self) -> tuple[StaleWorktreeItem, ...]:
        """Return active database records missing on disk."""
        return tuple(i for i in self.items if i.category == StaleWorktreeCategory.STALE_DB_RECORD)


@dataclass(frozen=True, slots=True)
class WorktreeCreation:
    """A created worktree session, with the warnings its metadata persist reported and their error-level records."""

    session: WorktreeSession
    warnings: tuple[str, ...] = ()
    diagnostics: tuple[DiagnosticRecord, ...] = ()


class WorktreeCleanupResult(BaseResult):
    """Result of removing a worktree, its branch and git's record of it; failed steps are warnings."""

    worktree_id: str = ""

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None


@dataclass(frozen=True, slots=True)
class WorktreeRemoval:
    """Warnings for the cleanup steps that failed, with their error-level records; empty when every step succeeded."""

    warnings: tuple[str, ...] = ()
    diagnostics: tuple[DiagnosticRecord, ...] = ()


class PruneAction(StrEnum):
    """Action taken on a detected stale resource during pruning."""

    PRUNED = "pruned"
    SKIPPED = "skipped"
    FAILED = "failed"


class WorktreePruneStatus(StrEnum):
    """Outcome status for worktree prune execution."""

    OK = "ok"
    PARTIAL_FAILURE = "partial_failure"


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

    status: WorktreePruneStatus | None = WorktreePruneStatus.OK
    dry_run: bool = False
    force: bool = False
    items: list[PrunedItem] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when prune reported no failure; partial_failure is not ok."""
        return self.error_code is None

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


class WorktreeDeleteResult(BaseResult):
    """Structured result for ``dovo worktree delete``; a failure is reported by error_code."""

    worktree_id: str = ""
    worktree: WorktreeRecord | None = None
    deleted: bool = False
    already_cleaned: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """True exactly when error_code is None."""
        return self.error_code is None
