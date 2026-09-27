"""Outcome models for session log inspection."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from worktree.common.models import BaseResult
from worktree.core.runtime import RunLogEvent


class LogsShowStatus(StrEnum):
    """Classified outcome for showing persisted session logs."""

    OK = "ok"
    SESSION_NOT_FOUND = "session_not_found"
    STEP_NOT_FOUND = "step_not_found"
    ATTEMPT_NOT_FOUND = "attempt_not_found"


class LogStreamFilter(StrEnum):
    """Which output stream(s) `wt logs --stream` selects."""

    STDOUT = "stdout"
    STDERR = "stderr"
    BOTH = "both"


class LogsShowResult(BaseResult):
    """Structured result for `wt logs` before rendering."""

    status: LogsShowStatus
    session_id: str | None = None
    lines: list[str] = Field(default_factory=list)
    events: list[RunLogEvent] = Field(default_factory=list)
    available_steps: list[str] = Field(default_factory=list)
    available_attempts: list[int] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when log content is available to render."""
        return self.status == LogsShowStatus.OK and not self.errors
