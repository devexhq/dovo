"""The worktree package's domain error and its fixed-code capacity subclass."""

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError


class WorktreeError(DomainError):
    """Business failure in a worktree operation, such as overlaying uncommitted changes into a worktree."""


class WorktreeCapacityError(WorktreeError):
    """Raised when active worktree count reaches configured capacity limit."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.WORKTREE_CAPACITY_EXCEEDED, message)
