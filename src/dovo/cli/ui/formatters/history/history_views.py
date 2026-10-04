from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.core.sessions.history.models import HistoryListStatus, HistoryShowStatus


class RunSummaryView(BaseModel):
    """Semantic view of an execution history run record."""

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
    runs: list[RunSummaryView] = Field(default_factory=list)
    total_runs: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class HistoryShowView(BaseModel):
    """Semantic view of execution history session detail."""

    model_config = {"extra": "forbid", "strict": True}

    status: HistoryShowStatus
    session_id: str | None = None
    run: RunSummaryView | None = None
    log_files: list[str] = Field(default_factory=list)
    log_snippet: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
