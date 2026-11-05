"""Workspace lock stand-ins for tests that simulate a lock held by another process."""

from __future__ import annotations

from itertools import count
from typing import cast

import pytest

from dovo.common.lock import LockTimeoutError, WorkspaceLock
from tests.harness.run_result import json_events


def raise_lock_timeout(_self: WorkspaceLock) -> WorkspaceLock:
    """Stand in for WorkspaceLock.__enter__ when another process holds the lock."""
    raise LockTimeoutError("lock held")


def announce_wait_then_time_out(self: WorkspaceLock) -> WorkspaceLock:
    """Stand in for WorkspaceLock.__enter__ when process 4242 holds the lock: report the wait through on_wait, then time out."""
    if self.on_wait is not None:
        self.on_wait(self.lock_path, "4242", self.timeout_seconds)
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
    events = json_events(stdout)
    if events:
        payload = cast(dict[str, object], events[-1]["payload"])
        return events[-1]["event_type"] == "error" and payload["error_code"] == "WORKSPACE_LOCK_TIMEOUT"
    return "Workspace Lock Timeout" in stdout
