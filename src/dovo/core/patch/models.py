"""Result types for validating or applying a unified diff."""

from enum import StrEnum

from pydantic import Field

from dovo.common.models import BaseResult


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
