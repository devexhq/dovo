"""Outcome models for session log inspection."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult


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
    events: list[RunLogEvent] = Field(default_factory=list)
    available_steps: list[str] = Field(default_factory=list)
    available_attempts: list[int] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when log content is available to render."""
        return self.status == LogsShowStatus.OK and not self.errors


class RunLogEventType(StrEnum):
    """Discriminates which optional fields a RunLogEvent populates."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    STEP_START = "step_start"
    STEP_DONE = "step_done"
    LOOP_START = "loop_start"
    LOOP_ITERATION_START = "loop_iteration_start"
    LOOP_CONDITIONS_EVALUATED = "loop_conditions_evaluated"
    LOOP_DONE = "loop_done"


class RunLogEvent(BaseModel):
    """One structured, ISO-timestamped run.log timeline record.

    The engine appends one JSON line per event and drops write failures, so a crash can leave a truncated trailing line;
    readers must skip lines that fail validation.
    """

    model_config = {"extra": "forbid", "strict": True}

    ts: str = ""
    event: RunLogEventType
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
    conditions: list[dict[str, object]] | None = None

    def details(self) -> dict[str, object]:
        """Return the populated scalar fields beyond ts and event, in declaration order; conditions are omitted."""
        return self.model_dump(exclude={"ts", "event", "conditions"}, exclude_none=True)
