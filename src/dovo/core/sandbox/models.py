"""Pydantic and Enum models for Git worktree sandboxes."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult
from dovo.core.db import SandboxRecord


class SandboxListStatus(StrEnum):
    """Classified outcome for ``dovo sandbox list``."""

    OK = "ok"
    NOT_INITIALIZED = "not_initialized"


class SandboxListResult(BaseResult):
    """Structured result for ``dovo sandbox list`` before rendering."""

    status: SandboxListStatus
    sandboxes: list[SandboxRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when listing can proceed (including empty tables)."""
        return self.status == SandboxListStatus.OK and not self.errors


class SandboxShowStatus(StrEnum):
    """Classified outcome for ``dovo sandbox show``."""

    OK = "ok"
    NOT_INITIALIZED = "not_initialized"
    NOT_FOUND = "not_found"


class SandboxShowResult(BaseResult):
    """Structured result for ``dovo sandbox show`` before rendering."""

    status: SandboxShowStatus
    sandbox: SandboxRecord | None = None
    disk_present: bool = False
    reconciled: bool = False

    @property
    def ok(self) -> bool:
        """True when a sandbox row is available to render."""
        return self.status == SandboxShowStatus.OK and self.sandbox is not None and not self.errors


class SandboxSession(BaseModel):
    """Metadata for one isolated background git worktree."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    target_branch: str
    sandbox_path: Path
    base_commit: str
    name: str | None = None
    created_at: str
    command_passed: bool | None = None
    wip_applied: bool = False
    wip_paths: list[str] = Field(default_factory=list)


class SandboxCreateStatus(StrEnum):
    """Classified outcomes for creating a sandbox worktree."""

    OK = "ok"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    GIT_FAILED = "git_failed"
    GIT_TIMEOUT = "git_timeout"
    NOT_INITIALIZED = "not_initialized"
    STORAGE_BRIDGE_FAILED = "storage_bridge_failed"
    UNREADABLE_CONFIG = "unreadable_config"
    WIP_FAILED = "wip_failed"


class SandboxCreateResult(BaseResult):
    """Non-raising result of sandbox creation."""

    status: SandboxCreateStatus
    session: SandboxSession | None = None

    @property
    def ok(self) -> bool:
        """Return True when a sandbox session was created successfully."""
        return self.status == SandboxCreateStatus.OK and not self.errors


class SandboxApplyStrategy(StrEnum):
    """Supported strategies for applying sandbox changes to the main workspace."""

    PATCH = "patch"
    SQUASH = "squash"


class SandboxApplyStatus(StrEnum):
    """Classified outcomes for applying a sandbox."""

    OK = "ok"
    NOT_FOUND = "not_found"
    ALREADY_MERGED = "already_merged"
    MAIN_REPO_DIRTY = "main_repo_dirty"
    EMPTY_DIFF = "empty_diff"
    CONFLICT = "conflict"
    GIT_FAILED = "git_failed"


class SandboxApplyResult(BaseResult):
    """Structured result of applying sandbox changes."""

    status: SandboxApplyStatus
    sandbox_id: str
    strategy: SandboxApplyStrategy = SandboxApplyStrategy.PATCH
    touched_files: list[str] = Field(default_factory=list)
    conflicting_files: list[str] = Field(default_factory=list)
    cleaned_up: bool = False
    commit_sha: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when changes were applied successfully without errors."""
        return self.status == SandboxApplyStatus.OK and not self.errors


class SandboxDiffStatus(StrEnum):
    """Classified outcomes for inspecting sandbox diffs."""

    OK = "ok"
    NOT_FOUND = "not_found"
    EMPTY_DIFF = "empty_diff"
    GIT_FAILED = "git_failed"


class SandboxDiffResult(BaseResult):
    """Structured result of inspecting sandbox diffs."""

    status: SandboxDiffStatus
    sandbox_id: str
    diff_text: str = ""
    stat_text: str = ""
    files_changed: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when diff was generated successfully without errors."""
        return self.status == SandboxDiffStatus.OK and not self.errors


class StaleSandboxCategory(StrEnum):
    """Classification category for stale sandbox resources."""

    STALE_WORKTREE_REF = "stale_worktree_ref"
    ORPHANED_DIRECTORY = "orphaned_directory"
    STALE_DB_RECORD = "stale_db_record"
    STALE_BRANCH = "stale_branch"


class StaleSandboxItem(BaseModel):
    """Detailed metadata for a detected stale or orphaned sandbox resource."""

    model_config = {"extra": "forbid", "strict": True}

    category: StaleSandboxCategory
    identifier: str
    path: Path | None = None
    branch_name: str | None = None
    session_id: str | None = None
    is_dirty: bool = False
    dirty_file_count: int = 0
    reason: str = ""


class SandboxDetectionStatus(StrEnum):
    """Outcome status for stale sandbox scanning."""

    OK = "ok"
    GIT_FAILED = "git_failed"
    UNREADABLE_CONFIG = "unreadable_config"
    ERROR = "error"


class SandboxDetectionResult(BaseResult):
    """Structured, non-raising result of stale sandbox detection."""

    status: SandboxDetectionStatus = SandboxDetectionStatus.OK
    items: list[StaleSandboxItem] = Field(default_factory=list)
    active_sandbox_count: int = 0

    @property
    def ok(self) -> bool:
        """Return True when detection completed without classified errors."""
        return self.status == SandboxDetectionStatus.OK and not self.errors

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
    def stale_worktrees(self) -> list[StaleSandboxItem]:
        """Return stale Git worktree registration items."""
        return [i for i in self.items if i.category == StaleSandboxCategory.STALE_WORKTREE_REF]

    @property
    def orphaned_directories(self) -> list[StaleSandboxItem]:
        """Return unindexed or non-active sandbox directory items."""
        return [i for i in self.items if i.category == StaleSandboxCategory.ORPHANED_DIRECTORY]

    @property
    def stale_db_records(self) -> list[StaleSandboxItem]:
        """Return active database records missing on disk."""
        return [i for i in self.items if i.category == StaleSandboxCategory.STALE_DB_RECORD]

    @property
    def stale_branches(self) -> list[StaleSandboxItem]:
        """Return unlinked sandbox branch items."""
        return [i for i in self.items if i.category == StaleSandboxCategory.STALE_BRANCH]


class PruneAction(StrEnum):
    """Action taken on a detected stale resource during pruning."""

    PRUNED = "pruned"
    SKIPPED = "skipped"
    FAILED = "failed"


class SandboxPruneStatus(StrEnum):
    """Outcome status for sandbox prune execution."""

    OK = "ok"
    PARTIAL_SUCCESS = "partial_success"
    GIT_FAILED = "git_failed"
    LOCKED = "locked"
    ERROR = "error"


class PrunedItem(BaseModel):
    """Details of a single resource processed during prune execution."""

    model_config = {"extra": "forbid", "strict": True}

    category: StaleSandboxCategory
    identifier: str
    action: PruneAction
    path: Path | None = None
    branch_name: str | None = None
    session_id: str | None = None
    reason: str = ""
    error: str | None = None


class SandboxPruneResult(BaseResult):
    """Structured result of sandbox pruning execution."""

    status: SandboxPruneStatus = SandboxPruneStatus.OK
    dry_run: bool = False
    force: bool = False
    items: list[PrunedItem] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True if prune completed cleanly without failures."""
        return self.status == SandboxPruneStatus.OK and not self.errors

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


class SandboxDeleteStatus(StrEnum):
    """Classified outcome for ``dovo sandbox delete``."""

    READY = "ready"
    DELETED = "deleted"
    ALREADY_CLEANED = "already_cleaned"
    ABORTED = "aborted"
    NOT_INITIALIZED = "not_initialized"
    NOT_FOUND = "not_found"


class SandboxDeleteResult(BaseResult):
    """Structured result for ``dovo sandbox delete``."""

    status: SandboxDeleteStatus
    sandbox_id: str = ""
    sandbox: SandboxRecord | None = None
    deleted: bool = False

    @property
    def ok(self) -> bool:
        """True when delete succeeded (deleted), ready, or already-cleaned."""
        return (
            self.status
            in {
                SandboxDeleteStatus.READY,
                SandboxDeleteStatus.DELETED,
                SandboxDeleteStatus.ALREADY_CLEANED,
            }
            and not self.errors
        )
