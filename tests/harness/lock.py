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


def lock_timeout_reported(stdout: str) -> bool:
    """Return whether stdout reports a lock timeout: the JSON `error` event last, or else the terminal panel."""
    lines = [line for line in stdout.splitlines() if line.startswith("{")]
    if lines:
        return lines[-1].startswith('{"event_type": "error"') and '"WORKSPACE_LOCK_TIMEOUT"' in lines[-1]
    return "Workspace Lock Timeout" in stdout
