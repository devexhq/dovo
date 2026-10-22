"""Frozen clock and event id for diagnostic log recorder tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Final
from uuid import UUID

import pytest

from dovo.common.filesystem.services import resolve_global_paths

FROZEN_EVENT_ID: Final[str] = "exc_3f9a1c07b2de"
FROZEN_DAY: Final[str] = "2026-10-19"
FROZEN_TIMESTAMP: Final[str] = "2026-10-19T14:03:22+00:00"


class _FrozenDatetime(datetime):
    """datetime whose now() is 2026-10-19T14:03:22+00:00 and whose astimezone() keeps that offset."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return cls(2026, 10, 19, 14, 3, 22, tzinfo=UTC)

    def astimezone(self, tz: tzinfo | None = None) -> _FrozenDatetime:
        return self


@pytest.fixture
def frozen_diagnostic_clock(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Freeze the recorder's clock and event ids; return the expected event file path under DOVO_HOME."""
    monkeypatch.setattr("dovo.common.diagnostic_log.services.record.datetime", _FrozenDatetime)
    monkeypatch.setattr(
        "dovo.common.diagnostic_log.services.record.uuid4",
        lambda: UUID("3f9a1c07b2de" + "0" * 20),
    )
    return resolve_global_paths().logs_dir / FROZEN_DAY / f"{FROZEN_EVENT_ID}.json"


def diagnostic_hint(event_file: Path) -> str:
    """Return the hint line, with its leading newline, that names the frozen event file."""
    return f"\nDetails logged: {event_file} (event {FROZEN_EVENT_ID})"


def read_single_event(event_file: Path) -> dict[str, object]:
    """Return the frozen event file's JSON, asserting it is the only record written that day."""
    assert list(event_file.parent.iterdir()) == [event_file]
    return json.loads(event_file.read_text(encoding="utf-8"))
