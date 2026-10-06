from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.core.sessions import HistoryListStatus, HistoryShowStatus


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


class HistoryListView(BaseModel):
    """Semantic view of execution history listing."""

    model_config = {"extra": "forbid", "strict": True}

    status: HistoryListStatus
    sessions: list[SessionSummaryView] = Field(default_factory=list)
    total_sessions: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class HistoryShowView(BaseModel):
    """Semantic view of execution history session detail."""

    model_config = {"extra": "forbid", "strict": True}

    status: HistoryShowStatus
    session_id: str | None = None
    session: SessionSummaryView | None = None
    log_files: list[str] = Field(default_factory=list)
    log_snippet: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
