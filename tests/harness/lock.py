"""Workspace lock stand-ins for tests that simulate a lock held by another process."""

from __future__ import annotations

from itertools import count

import pytest

from dovo.common.lock import LockTimeoutError, WorkspaceLock


def raise_lock_timeout(_self: WorkspaceLock) -> WorkspaceLock:
    """Stand in for WorkspaceLock.__enter__ when another process holds the lock."""
    raise LockTimeoutError("lock held")


def time_out_first_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch WorkspaceLock.__enter__ so its first acquisition raises LockTimeoutError("lock held") and every later one acquires normally."""
    original_enter = WorkspaceLock.__enter__
    calls = count()

    def _enter(self: WorkspaceLock) -> WorkspaceLock:
        if next(calls) == 0:
            raise LockTimeoutError("lock held")
        return original_enter(self)

    monkeypatch.setattr(WorkspaceLock, "__enter__", _enter)
