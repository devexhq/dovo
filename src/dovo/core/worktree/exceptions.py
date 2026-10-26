"""The worktree package's domain error, its fixed-code capacity and apply-conflict subclasses, and the cause helper for chaining a re-raised error."""

from collections.abc import Sequence

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError
from dovo.common.models import Remediation


class WorktreeError(DomainError):
    """Business failure in a worktree operation, such as overlaying uncommitted changes into a worktree."""


class WorktreeCapacityError(WorktreeError):
    """Raised when active worktree count reaches configured capacity limit."""

    def __init__(self, message: str, remediations: Sequence[Remediation] = ()) -> None:
        super().__init__(ErrorCode.WORKTREE_CAPACITY_EXCEEDED, message, remediations)


class WorktreeApplyConflictError(WorktreeError):
    """Raised when git apply --check reports conflicts; carries the conflicting paths."""

    def __init__(
        self, message: str, remediations: Sequence[Remediation] = (), conflicting_files: Sequence[str] = ()
    ) -> None:
        super().__init__(ErrorCode.WORKTREE_APPLY_CONFLICT, message, remediations)
        self.conflicting_files: tuple[str, ...] = tuple(conflicting_files)


def recorded_cause(exc: DomainError) -> BaseException:
    """Return the OSError exc was raised from, so catch_failure records it; else exc, which it does not record."""
    if isinstance(exc.__cause__, OSError):
        return exc.__cause__
    return exc
