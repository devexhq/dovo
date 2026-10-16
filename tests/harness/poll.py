"""Shared poll helper for tests that wait on an OS event with no virtual equivalent."""

from __future__ import annotations

import time
from collections.abc import Callable


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0, interval: float = 0.05) -> bool:
    """Return True once predicate holds, or False when timeout elapses; the only sleep tests may use."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
