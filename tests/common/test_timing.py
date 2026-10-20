"""Tests for the Stopwatch and TimeSpan timing primitives."""

from __future__ import annotations

from typing import NoReturn

import pytest
from pydantic import ValidationError

from dovo.common.timing import Stopwatch, TimeSpan

T0 = "2026-10-19T12:00:00+00:00"
T1 = "2026-10-19T12:00:01.500000+00:00"
T60 = "2026-10-19T12:01:00+00:00"


def _patch_clocks(monkeypatch: pytest.MonkeyPatch, *, monotonic: list[float], utc: list[str]) -> None:
    """Patch dovo.common.timing.monotonic and utc_now to return the given readings in order."""
    monotonic_readings = iter(monotonic)
    utc_readings = iter(utc)
    monkeypatch.setattr("dovo.common.timing.monotonic", lambda: next(monotonic_readings))
    monkeypatch.setattr("dovo.common.timing.utc_now", lambda: next(utc_readings))


def _forbid_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch both clocks to raise AssertionError when read."""

    def _fail() -> NoReturn:
        raise AssertionError("clock read")

    monkeypatch.setattr("dovo.common.timing.monotonic", _fail)
    monkeypatch.setattr("dovo.common.timing.utc_now", _fail)


class TimeSpanTests:
    def test_open_starts_now_and_is_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_clocks(monkeypatch, monotonic=[], utc=[T0])

        span = TimeSpan.open()

        assert span == TimeSpan(started_at=T0, ended_at=None, duration_seconds=None)
        assert span.is_open is True

    def test_closed_span_is_not_open(self) -> None:
        span = TimeSpan(started_at=T0, ended_at=T1, duration_seconds=1.5)

        assert span.is_open is False

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"started_at": T0, "ended_at": T1}, id="ended_without_duration"),
            pytest.param({"started_at": T0, "duration_seconds": 1.0}, id="duration_without_ended"),
            pytest.param({"started_at": T0, "ended_at": T1, "duration_seconds": -0.1}, id="negative_duration"),
            pytest.param({"started_at": "2026-10-19T12:00:00+02:00"}, id="started_non_utc"),
            pytest.param({"started_at": "2026-10-19T12:00:00"}, id="started_naive"),
            pytest.param({"started_at": "yesterday"}, id="started_unparseable"),
            pytest.param(
                {"started_at": T0, "ended_at": "2026-10-19T12:00:01+02:00", "duration_seconds": 1.0},
                id="ended_non_utc",
            ),
        ],
    )
    def test_invalid_span_fails_validation(self, fields: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            TimeSpan.model_validate(fields)

    def test_close_on_open_span_ends_now_with_wall_clock_duration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_clocks(monkeypatch, monotonic=[], utc=[T1])

        closed = TimeSpan(started_at=T0).close()

        assert closed == TimeSpan(started_at=T0, ended_at=T1, duration_seconds=1.5)

    def test_close_clamps_backwards_wall_clock_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_clocks(monkeypatch, monotonic=[], utc=[T0])

        closed = TimeSpan(started_at=T1).close()

        assert closed.duration_seconds == 0.0

    def test_close_on_closed_span_returns_it_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        span = TimeSpan(started_at=T0, ended_at=T1, duration_seconds=1.5)
        _forbid_clocks(monkeypatch)

        assert span.close() is span

    def test_span_is_frozen(self) -> None:
        span = TimeSpan(started_at=T0)

        with pytest.raises(ValidationError):
            span.ended_at = T1  # ty: ignore[invalid-assignment]  # intentional ill-typed test input: frozen-model write


class StopwatchTests:
    def test_elapsed_seconds_reads_live_monotonic_while_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_clocks(monkeypatch, monotonic=[10.0, 12.5], utc=[T0])

        watch = Stopwatch.start()

        assert watch.elapsed_seconds == 2.5

    def test_stop_returns_closed_span_with_monotonic_duration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Measure duration on the monotonic clock, ignoring the 60s gap between the UTC timestamps."""
        _patch_clocks(monkeypatch, monotonic=[10.0, 13.0], utc=[T0, T60])

        span = Stopwatch.start().stop()

        assert span == TimeSpan(started_at=T0, ended_at=T60, duration_seconds=3.0)

    def test_second_stop_returns_same_span_without_reading_clocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_clocks(monkeypatch, monotonic=[10.0, 13.0], utc=[T0, T60])
        watch = Stopwatch.start()
        first = watch.stop()
        _forbid_clocks(monkeypatch)

        assert watch.stop() is first
        assert watch.elapsed_seconds == 3.0
