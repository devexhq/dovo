"""Pydantic models for `.dovo/config.json` V1."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
from dovo.common.models import BaseResult

AgentProvider = Literal[
    "local",
    "ollama",
    "cursor",
    "gemini",
    "copilot",
    "openai",
    "anthropic",
    "azure_openai",
    "custom",
]


class ProjectConfig(BaseModel):
    """Project identity fields from config V1."""

    model_config = {"extra": "forbid", "strict": True}

    name: str
    initialized_at: str | None = None


class WorktreeConfig(BaseModel):
    """Background worktree lifecycle settings."""

    model_config = {"extra": "forbid", "strict": True}

    base_ref: str = Field(default="HEAD", min_length=1)
    max_active_worktrees: int = Field(default=DEFAULT_MAXIMUM_WORKTREES_ALLOWED, ge=1)
    default_timeout_seconds: int = Field(default=900, ge=1)


class AgentConfig(BaseModel):
    """Agent provider settings."""

    model_config = {"extra": "forbid", "strict": True}

    provider: AgentProvider = "local"
    model: str | None = Field(default=None, min_length=1)
    endpoint: str | None = Field(default=None, min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1)


class HistoryConfig(BaseModel):
    """Session history retention settings."""

    model_config = {"extra": "forbid", "strict": True}

    save_attempt_logs: bool = True
    save_agent_payloads: bool = True
    save_final_diff: bool = True
    max_sessions: int = Field(default=1000, ge=1)


class DoctorConfig(BaseModel):
    """Doctor command check toggles."""

    model_config = {"extra": "forbid", "strict": True}

    check_git: bool = True
    check_paths_writable: bool = True
    check_config_schema: bool = True
    check_stale_worktrees: bool = True
    check_required_binaries: bool = True


class PruneConfig(BaseModel):
    """Prune command cleanup toggles."""

    model_config = {"extra": "forbid", "strict": True}

    remove_stale_worktrees: bool = True
    remove_orphaned_worktrees: bool = True
    remove_expired_artifacts: bool = False
    artifact_ttl_days: int = Field(default=30, ge=0)


class TelemetryConfig(BaseModel):
    """Optional telemetry settings."""

    model_config = {"extra": "forbid", "strict": True}

    enabled: bool = False


class ConcurrencyConfig(BaseModel):
    """Concurrency and locking settings."""

    model_config = {"extra": "forbid", "strict": True}

    lock_timeout_seconds: float = Field(default=30.0, ge=0.1)


class DovoConfig(BaseModel):
    """Parsed `.dovo/config.json` V1 payload."""

    model_config = {"extra": "forbid", "strict": True}

    version: int
    project: ProjectConfig
    ignore_global_root_error: bool = False
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)
    prune: PruneConfig = Field(default_factory=PruneConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)

    @property
    def project_name(self) -> str:
        """Compatibility alias for project display name."""
        return self.project.name


class ConfigTier(StrEnum):
    """Precedence tiers for hierarchical configuration resolution."""

    PACKAGED = "packaged"
    GLOBAL = "global"
    USER = "user"
    REPO = "repo"


class ConfigLayer(BaseModel):
    """One resolved configuration tier: its precedence, source path, and raw data."""

    model_config = {"extra": "forbid", "strict": True}

    tier: ConfigTier
    path: Path | None
    data: dict[str, Any]


class HierarchicalConfigLoadStatus(StrEnum):
    """Classified outcomes for resolving and merging the hierarchical DovoConfig tiers."""

    OK = "ok"
    UNREADABLE = "unreadable"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    VALIDATION_FAILED = "validation_failed"


class HierarchicalConfigLoadResult(BaseResult):
    """Non-raising result of resolving and merging the Packaged, Global, User, and Repo config tiers."""

    status: HierarchicalConfigLoadStatus
    tier: ConfigTier | None = None
    path: Path | None = None
    config: DovoConfig | None = None

    @property
    def ok(self) -> bool:
        """Return True when every tier read and validated successfully."""
        return self.status == HierarchicalConfigLoadStatus.OK
