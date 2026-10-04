from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.core.sessions.diff.models import DiffStatus


class DiffResultView(BaseModel):
    """Semantic view of diff command output."""

    model_config = {"extra": "forbid", "strict": True}

    status: DiffStatus
    session_id: str | None = None
    artifact_path: Path | None = None
    relative_path: str = ""
    diff_text: str = ""
    raw: bool = False
    full: bool = False
    max_lines: int | None = None
    total_lines: int = 0
    truncated: bool = False
    truncated_lines: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
