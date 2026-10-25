"""Semantic view models for session formatters."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.models import Remediation
from dovo.core.sessions import (
    DiffStatus,
    HistoryListStatus,
    HistoryShowStatus,
    LogsShowStatus,
    SessionLogEvent,
    StepRunSummary,
)


class SessionSummaryView(BaseModel):
    """Semantic view of an execution history session record."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    blueprint_name: str
    status: str
    branch_name: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None


class SessionListView(BaseModel):
    """Semantic view of the session listing."""

    model_config = {"extra": "forbid", "strict": True}

    status: HistoryListStatus
    sessions: list[SessionSummaryView] = Field(default_factory=list)
    total_sessions: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)


class SessionShowView(BaseModel):
    """Semantic view of one session's detail."""

    model_config = {"extra": "forbid", "strict": True}

    status: HistoryShowStatus
    session_id: str | None = None
    session: SessionSummaryView | None = None
    log_files: list[str] = Field(default_factory=list)
    log_snippet: list[str] = Field(default_factory=list)
    steps: list[StepRunSummary] = Field(default_factory=list)
    worktree_kept: bool | None = None
    worktree_path: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)


class SessionLogsView(BaseModel):
    """Semantic view of a session's session.log events or step log lines."""

    model_config = {"extra": "forbid", "strict": True}

    status: LogsShowStatus
    session_id: str | None = None
    lines: list[str] = Field(default_factory=list)
    events: list[SessionLogEvent] = Field(default_factory=list)
    available_steps: list[str] = Field(default_factory=list)
    available_attempts: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)


class SessionDiffView(BaseModel):
    """Semantic view of session diff output."""

    model_config = {"extra": "forbid", "strict": True}

    status: DiffStatus
    session_id: str | None = None
    artifact_path: Path | None = None
    relative_path: str = ""
    diff_text: str = ""
    full: bool = False
    max_lines: int | None = None
    # Presentation-only header switch; excluded so it never reaches the JSON wire payload.
    latest: bool = Field(default=False, exclude=True)
    total_lines: int = 0
    truncated: bool = False
    truncated_lines: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
