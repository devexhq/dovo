"""Domain models for workspace status collection."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult, Remediation
from dovo.core.config.models import DovoConfig


class WorkspaceHealth(StrEnum):
    """Overall workspace health: an outcome, never a failure cause."""

    OK = "ok"
    DEGRADED = "degraded"
    UNINITIALIZED = "uninitialized"


class GitStatusInfo(BaseModel):
    """Git repository branch and working tree status, with the code, warning and record of a git that could not be read."""

    model_config = {"extra": "forbid", "strict": True}

    is_git_repo: bool
    branch: str
    is_dirty: bool
    uncommitted_files: int
    error_code: ErrorCode | None = None  # GIT_TIMEOUT or GIT_BINARY_MISSING; None outside a repository
    warning: str | None = None
    diagnostic: DiagnosticRecord | None = None


class ConfigStatusInfo(BaseModel):
    """Configuration file health and parsed model."""

    model_config = {"extra": "forbid", "strict": True}

    error_code: str | None
    config_path: Path
    is_valid: bool
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None
    errors: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)


class CatalogStatusInfo(BaseModel):
    """Catalog blueprint directory health."""

    model_config = {"extra": "forbid", "strict": True}

    exists: bool
    catalog_dir: Path
    total_items: int
    blueprints_count: int
    steps_count: int
    invalid_items: int
    item_names: list[str] = Field(default_factory=list)


class DatabaseStatusInfo(BaseModel):
    """Centralized SQLite database status and run metrics, with the record of a failed read."""

    model_config = {"extra": "forbid", "strict": True}

    exists: bool
    db_path: Path
    is_accessible: bool
    total_sessions: int = 0
    diagnostic: DiagnosticRecord | None = None


class WorktreeStatusInfo(BaseModel):
    """Worktree inventory and concurrency metrics, with the warning and record of a failed database read."""

    model_config = {"extra": "forbid", "strict": True}

    active_worktrees: int
    total_worktrees: int
    max_active_worktrees: int
    warning: str | None = None
    diagnostic: DiagnosticRecord | None = None


class DovoStatusResult(BaseResult):
    """Unified workspace status collection result; health is reported in status, never as a failure."""

    status: WorkspaceHealth
    root_dir: Path
    is_initialized: bool
    git: GitStatusInfo
    config: ConfigStatusInfo
    catalog: CatalogStatusInfo
    database: DatabaseStatusInfo
    worktrees: WorktreeStatusInfo
