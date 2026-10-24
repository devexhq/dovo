"""Workspace lock stand-ins for tests that simulate a lock held by another process."""

from __future__ import annotations

from dovo.common.lock import LockTimeoutError, WorkspaceLock


def raise_lock_timeout(_self: WorkspaceLock) -> WorkspaceLock:
    """Stand in for WorkspaceLock.__enter__ when another process holds the lock."""
    raise LockTimeoutError("lock held")
