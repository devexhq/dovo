"""Outcome models and the session.log event model for session operations."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult
from dovo.core.db import SessionRecord


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


class SessionLogEventType(StrEnum):
    """Discriminates which optional fields a SessionLogEvent populates."""

    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    STEP_START = "step_start"
    STEP_DONE = "step_done"
    LOOP_START = "loop_start"
    LOOP_ITERATION_START = "loop_iteration_start"
    LOOP_CONDITIONS_EVALUATED = "loop_conditions_evaluated"
    LOOP_DONE = "loop_done"
    AGENT_ENV_FILTERED = "agent_env_filtered"


class SessionLogEvent(BaseModel):
    """One structured, ISO-timestamped session.log timeline record.

    The engine appends one JSON line per event and drops write failures, so a crash can leave a truncated trailing line;
    readers must skip lines that fail validation.
    """

    model_config = {"extra": "forbid", "strict": True}

    ts: str = ""
    event: SessionLogEventType
    session_id: str | None = None
    blueprint_key: str | None = None
    step_index: int | None = None
    step_id: str | None = None
    step_name: str | None = None
    attempt: int | None = None
    status: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    loop_id: str | None = None
    iteration: int | None = None
    max_iterations: int | None = None
    all_passed: bool | None = None
    next_iteration: int | None = None
    env_withheld: str | None = None
    conditions: list[dict[str, object]] | None = None

    def details(self) -> dict[str, object]:
        """Return the populated scalar fields beyond ts and event, in declaration order; conditions are omitted."""
        return self.model_dump(exclude={"ts", "event", "conditions"}, exclude_none=True)


class LogsShowStatus(StrEnum):
    """Classified outcome for showing persisted session logs."""

    OK = "ok"
    SESSION_NOT_FOUND = "session_not_found"
    STEP_NOT_FOUND = "step_not_found"
    ATTEMPT_NOT_FOUND = "attempt_not_found"


class LogStreamFilter(StrEnum):
    """Which output stream(s) `dovo logs --stream` selects."""

    STDOUT = "stdout"
    STDERR = "stderr"
    BOTH = "both"


class LogsShowResult(BaseResult):
    """Structured result for `dovo logs` before rendering."""

    status: LogsShowStatus
    session_id: str | None = None
    lines: list[str] = Field(default_factory=list)
    events: list[SessionLogEvent] = Field(default_factory=list)
    available_steps: list[str] = Field(default_factory=list)
    available_attempts: list[int] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when log content is available to render."""
        return self.status == LogsShowStatus.OK and not self.errors


class HistoryListStatus(StrEnum):
    """Classified outcome for listing execution history."""

    OK = "ok"


class HistoryListResult(BaseResult):
    """Structured result for history list before rendering."""

    status: HistoryListStatus = HistoryListStatus.OK
    sessions: list[SessionRecord] = Field(default_factory=list)

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
    session: SessionRecord | None = None
    log_files: list[str] = Field(default_factory=list)
    log_snippet: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when a session record is available to render."""
        return self.status == HistoryShowStatus.OK and self.session is not None and not self.errors


class ReconciliationResult(BaseResult):
    """Result of reconciling stale running sessions."""

    reconciled: list[SessionRecord] = Field(default_factory=list)
    warning: str | None = None
