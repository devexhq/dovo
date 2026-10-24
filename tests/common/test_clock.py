"""Tests for the UTC clock."""

from __future__ import annotations

from datetime import timedelta

from dovo.common.clock import utc_now


class UtcNowTests:
    def test_utc_now_returns_aware_datetime_with_zero_utc_offset(self) -> None:
        """Return a datetime whose utcoffset() is timedelta(0)."""
        assert utc_now().utcoffset() == timedelta(0)
