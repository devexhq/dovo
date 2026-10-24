"""The one source of the current UTC time: injected into domain entrypoints by the CLI composition root, and read by common/timing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)
