"""Domain exceptions for worktree lifecycle and patch integration."""

from __future__ import annotations


class WorktreeError(RuntimeError):
    """Base exception for all worktree domain operations."""


class WorktreeConfigError(WorktreeError):
    """Raised when worktree configuration is missing or invalid."""


class WorktreeCapacityError(WorktreeError):
    """Raised when active worktree count reaches configured capacity limit."""
