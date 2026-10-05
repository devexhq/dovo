from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from dovo.core.config.loader import ConfigLoadStatus


class StatusHealth(StrEnum):
    """Overall workspace health, the axis the table title communicates."""

    OK = "ok"
    DEGRADED = "degraded"
    UNINITIALIZED = "uninitialized"


class StatusView(BaseModel):
    """Semantic view of a status result: no Rich markup, no composed sentences."""

    model_config = {"extra": "forbid", "strict": True}

    health: StatusHealth
    root_dir: Path
    project_name: str | None
    config_status: ConfigLoadStatus
    config_path_relative: str
    git_branch: str | None
    git_is_dirty: bool
    uncommitted_files: int
    agent_model: str | None
    active_worktrees: int | None
    max_active_worktrees: int | None
    valid_catalog_items: int | None
    total_catalog_items: int | None
    total_sessions: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[str] = Field(default_factory=list)
