"""The worktree package's domain error, its fixed-code capacity subclass, its apply-conflict failure data, and the cause helper for chaining a re-raised error."""

from collections.abc import Sequence
from dataclasses import dataclass

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError, FailureData
from dovo.common.models import Remediation


class WorktreeError(DomainError):
    """Business failure in a worktree operation, such as overlaying uncommitted changes into a worktree."""


class WorktreeCapacityError(WorktreeError):
    """Raised when active worktree count reaches configured capacity limit."""

    def __init__(self, message: str, remediations: Sequence[Remediation] = ()) -> None:
        super().__init__(ErrorCode.WORKTREE_CAPACITY_EXCEEDED, message, remediations)


@dataclass(frozen=True, slots=True)
class WorktreeApplyConflict(FailureData):
    """A WORKTREE_APPLY_CONFLICT failure's data: the paths git apply --check reported as conflicting."""

    conflicting_files: tuple[str, ...]


def recorded_cause(exc: DomainError) -> BaseException:
    """Return the OSError exc was raised from, so catch_failure records it; else exc, which it does not record."""
    if isinstance(exc.__cause__, OSError):
        return exc.__cause__
    return exc
