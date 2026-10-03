"""Domain models for workspace status collection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.config.models import DovoConfig


class GitStatusInfo(BaseModel):
    """Git repository branch and working tree status."""

    model_config = {"extra": "forbid", "strict": True}

    is_git_repo: bool
    branch: str
    is_dirty: bool
    uncommitted_files: int


class ConfigStatusInfo(BaseModel):
    """Configuration file health and parsed model."""

    model_config = {"extra": "forbid", "strict": True}

    status: ConfigLoadStatus
    config_path: Path
    is_valid: bool
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None
    errors: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class CatalogStatusInfo(BaseModel):
    """Catalog blueprint directory health."""

    model_config = {"extra": "forbid", "strict": True}

    exists: bool
    catalog_dir: Path
    total_items: int
    workflows_count: int
    tasks_count: int
    steps_count: int
    invalid_items: int
    item_names: list[str] = Field(default_factory=list)


class DatabaseStatusInfo(BaseModel):
    """Centralized SQLite database status and run metrics."""

    model_config = {"extra": "forbid", "strict": True}

    exists: bool
    db_path: Path
    is_accessible: bool
    total_runs: int = 0


class WorktreeStatusInfo(BaseModel):
    """Worktree inventory and concurrency metrics."""

    model_config = {"extra": "forbid", "strict": True}

    active_worktrees: int
    total_worktrees: int
    max_active_worktrees: int


class DovoStatusResult(BaseResult):
    """Unified workspace status collection result."""

    root_dir: Path
    is_initialized: bool
    git: GitStatusInfo
    config: ConfigStatusInfo
    catalog: CatalogStatusInfo
    database: DatabaseStatusInfo
    worktrees: WorktreeStatusInfo

    @property
    def ok(self) -> bool:
        """True when workspace is a valid git repo, valid config, accessible DB, and zero invalid catalog items."""
        return (
            self.git.is_git_repo
            and self.config.is_valid
            and self.database.is_accessible
            and self.catalog.invalid_items == 0
        )
