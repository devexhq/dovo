"""Outcome models for diff operations."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from dovo.common.models import BaseResult


class DiffStatus(StrEnum):
    """Classified outcomes for retrieving session diff."""

    OK = "ok"
    EMPTY_DIFF = "empty_diff"
    SESSION_NOT_FOUND = "session_not_found"
    DIFF_NOT_FOUND = "diff_not_found"
    READ_FAILURE = "read_failure"


class DiffResult(BaseResult):
    """Structured result of diff collection before rendering."""

    status: DiffStatus
    session_id: str | None = None
    artifact_path: Path | None = None
    diff_text: str = ""
    raw: bool = False
    full: bool = False
    max_lines: int | None = None

    @property
    def ok(self) -> bool:
        """True when diff retrieved successfully or clean empty diff."""
        return self.status in (DiffStatus.OK, DiffStatus.EMPTY_DIFF) and not self.errors
