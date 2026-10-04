"""Outcome models for history operations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from dovo.common.models import BaseResult
from dovo.core.db import RunRecord


class HistoryListStatus(StrEnum):
    """Classified outcome for listing execution history."""

    OK = "ok"


class HistoryListResult(BaseResult):
    """Structured result for history list before rendering."""

    status: HistoryListStatus = HistoryListStatus.OK
    runs: list[RunRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when listing can proceed (including empty tables)."""
        return self.status == HistoryListStatus.OK and not self.errors


class HistoryShowStatus(StrEnum):
    """Classified outcome for showing history session detail."""

    OK = "ok"
    NOT_FOUND = "not_found"


class HistoryShowResult(BaseResult):
    """Structured result for history show before rendering."""

    status: HistoryShowStatus
    session_id: str | None = None
    run: RunRecord | None = None
    log_files: list[str] = Field(default_factory=list)
    log_snippet: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when a run record is available to render."""
        return self.status == HistoryShowStatus.OK and self.run is not None and not self.errors


class ReconciliationResult(BaseResult):
    """Result of reconciling stale running sessions."""

    reconciled: list[RunRecord] = Field(default_factory=list)
    warning: str | None = None
