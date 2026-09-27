from __future__ import annotations

from pydantic import BaseModel, Field

from worktree.core.logs.models import LogsShowStatus
from worktree.core.runtime import RunLogEvent


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
