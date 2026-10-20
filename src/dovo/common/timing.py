"""Elapsed-time measurement: a monotonic Stopwatch and the UTC TimeSpan it produces."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _require_utc(value: str) -> str:
    """Return value unchanged when it parses as an ISO-8601 timestamp with a zero UTC offset, else raise ValueError."""
    offset = datetime.fromisoformat(value).utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(f"timestamp {value!r} is not UTC")
    return value


class TimeSpan(BaseModel):
    """A UTC interval that is open (not yet ended) or closed (ended, with a duration in seconds)."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    started_at: str
    ended_at: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)

    @field_validator("started_at", "ended_at")
    @classmethod
    def _timestamps_are_utc(cls, value: str | None) -> str | None:
        """Reject a timestamp that is not ISO-8601 UTC; pass None through."""
        if value is None:
            return None
        return _require_utc(value)

    @model_validator(mode="after")
    def _check_shape(self) -> TimeSpan:
        """Reject a span where exactly one of ended_at and duration_seconds is set."""
        if (self.ended_at is None) != (self.duration_seconds is None):
            raise ValueError("a TimeSpan is open (ended_at and duration_seconds unset) or closed (both set)")
        return self

    @classmethod
    def open(cls) -> TimeSpan:
        """Return an open span starting now."""
        return cls(started_at=utc_now())

    @property
    def is_open(self) -> bool:
        """Return True when the span has not been closed."""
        return self.ended_at is None

    def close(self) -> TimeSpan:
        """Return this span closed now with a wall-clock duration clamped at 0.0, or itself when already closed."""
        if not self.is_open:
            return self
        ended = utc_now()
        seconds = (datetime.fromisoformat(ended) - datetime.fromisoformat(self.started_at)).total_seconds()
        return TimeSpan(started_at=self.started_at, ended_at=ended, duration_seconds=max(0.0, seconds))


class Stopwatch:
    """Measures one elapsed interval on the monotonic clock, stamped with UTC start and end times."""

    def __init__(self, started: float, started_at: str) -> None:
        """Hold the monotonic start reading and its UTC timestamp; use Stopwatch.start() instead."""
        self._started = started
        self._started_at = started_at
        self._span: TimeSpan | None = None
        self._elapsed: float | None = None

    @classmethod
    def start(cls) -> Stopwatch:
        """Return a running stopwatch started now."""
        started = monotonic()
        return cls(started, utc_now())

    @property
    def elapsed_seconds(self) -> float:
        """Return live monotonic elapsed seconds while running, or the frozen duration once stopped."""
        if self._elapsed is not None:
            return self._elapsed
        return monotonic() - self._started

    def stop(self) -> TimeSpan:
        """Return the closed span, measuring it on the first call and reading no clock on later calls."""
        if self._span is not None:
            return self._span
        elapsed = max(0.0, monotonic() - self._started)
        span = TimeSpan(started_at=self._started_at, ended_at=utc_now(), duration_seconds=elapsed)
        self._elapsed = elapsed
        self._span = span
        return span
