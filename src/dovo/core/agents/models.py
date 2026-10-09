"""Agent request/response DTOs, failure payloads, and resolved settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from dovo.common.models import BaseResult
from dovo.common.tool_policy import ToolCapability, ToolPolicy

OmissionReason = Literal[
    "missing",
    "outside_worktree",
    "directory",
    "binary",
    "max_files",
    "max_file_bytes",
]

ENV_PASSTHROUGH_PATTERN = r"^[^=\x00*\s]+\*?$"
EnvPassthroughEntry = Annotated[str, StringConstraints(pattern=ENV_PASSTHROUGH_PATTERN)]
AgentEnvMode = Literal["allowlist", "inherit"]


class PayloadOmission(BaseModel):
    """Record of a candidate path that was not included in the payload."""

    model_config = {"extra": "forbid", "strict": True}

    path: str
    reason: OmissionReason


class PayloadFile(BaseModel):
    """Worktree-relative source file content attached to a failure payload."""

    model_config = {"extra": "forbid", "strict": True}

    path: str
    content: str
    truncated: bool = False


class AgentFailurePayload(BaseModel):
    """Bounded context package for an agent fix request."""

    model_config = {"extra": "forbid", "strict": True}

    command: str
    args: list[str]
    trigger_status: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    changed_files: list[str] = Field(default_factory=list)
    files: list[PayloadFile] = Field(default_factory=list)
    omissions: list[PayloadOmission] = Field(default_factory=list)


class AgentEnvOverrides(BaseModel):
    """Per-invocation environment overrides from CLI flags; never persisted to config or run state."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    env_mode: AgentEnvMode | None = None
    env_passthrough: list[EnvPassthroughEntry] = Field(default_factory=list)


class ResolvedAgentSettings(BaseModel):
    """Effective agent settings for one run drive; carries no credential values."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    provider: str
    model: str | None
    endpoint: str | None
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    env_passthrough: list[EnvPassthroughEntry] = Field(default_factory=list)
    env_mode: AgentEnvMode = "allowlist"
    tools: ToolPolicy


class AgentResponseStatus(StrEnum):
    """Normalized outcomes from an agent adapter call."""

    PROPOSED_PATCH = "proposed_patch"
    NO_OP = "no_op"
    UNFIXABLE = "unfixable"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    BLOCKED = "blocked"


class AgentDenial(BaseModel):
    """One tool call the provider refused, with the capability it maps to when known."""

    model_config = {"extra": "forbid", "strict": True}

    tool: str
    message: str
    capability: ToolCapability | None = None
    by_rule: bool = False


class AgentInvocationContext(BaseModel):
    """Private per-attempt paths allocated at the step boundary; control_path is host-owned and never reaches prompts, environments, or grants."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scratch_path: Path
    control_path: Path


class AgentAttemptContext(BaseModel):
    """Step-boundary inputs for one direct attempt: the private invocation paths and the process-environment layers."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    invocation: AgentInvocationContext | None = None
    env: dict[str, str] = Field(default_factory=dict)
    metadata_env: dict[str, str] = Field(default_factory=dict)


class AgentScratchResult(BaseResult):
    """Outcome of allocating one attempt's scratch and control directories; the id is retained on failure."""

    invocation_id: str
    context: AgentInvocationContext | None = None

    @property
    def ok(self) -> bool:
        """Return True when both directories were created and verified."""
        return self.context is not None


class AgentRequest(BaseModel):
    """Input package for ``BaseAgentProvider.invoke``."""

    model_config = {"extra": "forbid", "strict": True}

    mode: Literal["direct", "fix_failure", "review_remediation"]
    instruction: str = Field(min_length=1)
    payload: AgentFailurePayload | None = None
    worktree_path: Path
    timeout_seconds: int = Field(ge=1)
    model: str | None = None
    endpoint: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_files: int | None = None
    max_patch_kb: int | None = None
    reject_binary_changes: bool | None = None
    agent_scratch_path: Path | None = None
    invocation: AgentInvocationContext | None = Field(default=None, exclude=True)
    env_passthrough: list[EnvPassthroughEntry] = Field(default_factory=list)
    env_mode: AgentEnvMode = "allowlist"
    tools: ToolPolicy
    env: dict[str, str] = Field(default_factory=dict, exclude=True)
    metadata_env: dict[str, str] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def validate_mode_contract(self) -> AgentRequest:
        """Reject a blank instruction, a direct request with a payload, a remediation request without one, and a scratch path that differs from the invocation's."""
        if not self.instruction.strip():
            raise ValueError("AgentRequest.instruction must not be blank.")

        if self.mode == "direct" and self.payload is not None:
            raise ValueError("AgentRequest mode 'direct' must not carry a failure payload.")

        if self.mode != "direct" and self.payload is None:
            raise ValueError(f"AgentRequest mode '{self.mode}' requires a failure payload.")

        if (
            self.agent_scratch_path is not None
            and self.invocation is not None
            and self.agent_scratch_path != self.invocation.scratch_path
        ):
            raise ValueError("AgentRequest.agent_scratch_path must equal invocation.scratch_path.")

        return self


class AgentResponse(BaseModel):
    """Normalized result of an agent fix proposal."""

    model_config = {"extra": "forbid", "strict": True}

    status: AgentResponseStatus
    unified_diff: str | None = None
    summary: str | None = None
    unfixable_reason: str | None = None
    raw_text: str | None = None
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
    mutation_baseline_ref: str | None = None
    env_withheld: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True only when a patch was proposed."""
        return self.status == AgentResponseStatus.PROPOSED_PATCH


@dataclass(frozen=True)
class AgentAttempt:
    """Classified result of one agent attempt before an adapter turns it into a caller-specific outcome."""

    status: AgentResponseStatus
    summary: str | None = None
    unfixable_reason: str | None = None
    touched_files: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    env_withheld: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """Return True for PROPOSED_PATCH and NO_OP."""
        return self.status in {AgentResponseStatus.PROPOSED_PATCH, AgentResponseStatus.NO_OP}
