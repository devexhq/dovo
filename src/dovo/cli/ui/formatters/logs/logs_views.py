from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.core.logs import RunLogEvent
from dovo.core.logs.models import LogsShowStatus


class LogsShowView(BaseModel):
    """Semantic view of a session's run.log events or step log lines."""

    model_config = {"extra": "forbid", "strict": True}

    status: LogsShowStatus
    session_id: str | None = None
    lines: list[str] = Field(default_factory=list)
    events: list[RunLogEvent] = Field(default_factory=list)
    available_steps: list[str] = Field(default_factory=list)
    available_attempts: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
