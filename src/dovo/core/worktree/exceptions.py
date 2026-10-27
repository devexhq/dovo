"""The worktree package's domain error and its apply-conflict failure data."""

from dataclasses import dataclass

from dovo.common.exceptions import DomainError, FailureData


class WorktreeError(DomainError):
    """Business failure in a worktree operation, such as overlaying uncommitted changes into a worktree."""


@dataclass(frozen=True, slots=True)
class WorktreeApplyConflict(FailureData):
    """A WORKTREE_APPLY_CONFLICT failure's data: the paths git apply --check reported as conflicting."""

    conflicting_files: tuple[str, ...]
