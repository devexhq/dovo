"""Pydantic models for `.dovo/config.json` V1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_validator,
    model_serializer,
)

from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
from dovo.common.mcp_servers import McpServerDefinition, validate_mcp_server_name
from dovo.common.models import BaseResult
from dovo.common.tool_policy import ToolPolicy, coerce_tool_policy
from dovo.core.agents.models import AgentEnvMode, EnvPassthroughEntry

AgentProvider = Literal["copilot", "claude"]
DEFAULT_AGENT: Final[AgentProvider] = "copilot"


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


class AgentSettings(BaseModel):
    """Settings for one agent provider, stored under ``agents.<provider>``."""

    model_config = {"extra": "forbid", "strict": True}

    model: str | None = Field(default=None, min_length=1)
    endpoint: str | None = Field(default=None, min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1)
    env_passthrough: list[EnvPassthroughEntry] = Field(default_factory=list)
    env_mode: AgentEnvMode = "allowlist"
    mcp_servers: dict[str, McpServerDefinition] = Field(default_factory=dict)
    tools: ToolPolicy | None = None

    @field_validator("mcp_servers")
    @classmethod
    def validate_mcp_server_names(cls, servers: dict[str, McpServerDefinition]) -> dict[str, McpServerDefinition]:
        """Reject a server name that cannot be an mcp allow-rule or grant key."""
        for name in servers:
            reason = validate_mcp_server_name(name)
            if reason is not None:
                raise ValueError(reason)

        return servers

    @field_validator("tools", mode="before")
    @classmethod
    def reject_legacy_tools(cls, val: object) -> object:
        """Reject the legacy string list with migration guidance."""
        return coerce_tool_policy(val)

    @model_serializer(mode="wrap")
    def omit_unset_tools(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Drop a null tools key: the config schema accepts an object or an absent key, not null."""
        data: dict[str, Any] = handler(self)
        if data.get("tools") is None:
            data.pop("tools", None)
        return data


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


SensitiveVariableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class EnvironmentConfig(BaseModel):
    """`environment` section of `.dovo/config.json`."""

    model_config = {"extra": "forbid", "strict": True}

    sensitive_variables: list[SensitiveVariableName] = Field(default_factory=list)


class DovoConfig(BaseModel):
    """Parsed `.dovo/config.json` V1 payload."""

    model_config = {"extra": "forbid", "strict": True}

    version: int
    project: ProjectConfig
    ignore_global_root_error: bool = False
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    default_agent: AgentProvider = DEFAULT_AGENT
    agents: dict[AgentProvider, AgentSettings] = Field(default_factory=dict)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)
    prune: PruneConfig = Field(default_factory=PruneConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)

    @property
    def project_name(self) -> str:
        """Compatibility alias for project display name."""
        return self.project.name


@dataclass(frozen=True, slots=True)
class ConfigGenerationSummary:
    """What generating `.dovo/config.json` did: created, skipped, repaired or overwritten it."""

    config_path: Path
    created: bool
    skipped_existing: bool
    repaired: bool
    overwritten: bool
    inserted_keys: tuple[str, ...]
    warnings: tuple[str, ...]


class ConfigTier(StrEnum):
    """Precedence tiers for tiered configuration resolution."""

    PACKAGED = "packaged"
    GLOBAL = "global"
    USER = "user"
    REPO = "repo"


class ConfigTierEntry(BaseModel):
    """One resolved configuration tier: its precedence, source path, and raw data."""

    model_config = {"extra": "forbid", "strict": True}

    tier: ConfigTier
    path: Path | None
    data: dict[str, Any]


class TieredConfigLoadStatus(StrEnum):
    """Classified outcomes for resolving and merging the DovoConfig tiers."""

    OK = "ok"
    UNREADABLE = "unreadable"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    VALIDATION_FAILED = "validation_failed"


class TieredConfigLoadResult(BaseResult):
    """Non-raising result of resolving and merging the Packaged, Global, User, and Repo config tiers."""

    status: TieredConfigLoadStatus
    tier: ConfigTier | None = None
    path: Path | None = None
    config: DovoConfig | None = None

    @property
    def ok(self) -> bool:
        """Return True when every tier read and validated successfully."""
        return self.status == TieredConfigLoadStatus.OK
