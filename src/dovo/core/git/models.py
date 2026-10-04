"""Pydantic models for low-level Git operations and unified-diff validation results."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult


class GitWorktreeEntry(BaseModel):
    """Parsed entry from `git worktree list --porcelain`."""

    model_config = {"extra": "forbid", "strict": True}

    path: Path
    head_sha: str = ""
    branch: str | None = None
    is_bare: bool = False
    is_detached: bool = False
    is_locked: bool = False
    is_prunable: bool = False
    prunable_reason: str | None = None


class PatchApplyStatus(StrEnum):
    """Classified outcomes for validating or applying a unified diff."""

    APPLIED = "applied"
    CHECKED_OK = "checked_ok"
    EMPTY_DIFF = "empty_diff"
    TOO_LARGE = "too_large"
    TOO_MANY_FILES = "too_many_files"
    BINARY_REJECTED = "binary_rejected"
    UNSAFE_PATH = "unsafe_path"
    INVALID_DIFF = "invalid_diff"
    CONFLICT = "conflict"
    GIT_TIMEOUT = "git_timeout"
    WORKTREE_MISSING = "worktree_missing"


class PatchApplyResult(BaseResult):
    """Non-raising result of patch validation / apply."""

    status: PatchApplyStatus
    touched_files: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when the patch applied or dry-check succeeded."""
        return self.status in {
            PatchApplyStatus.APPLIED,
            PatchApplyStatus.CHECKED_OK,
        }
