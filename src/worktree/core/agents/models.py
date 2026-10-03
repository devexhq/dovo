"""Agent request/response DTOs, failure payloads, provider kind, and resolved settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

OmissionReason = Literal[
    "missing",
    "outside_sandbox",
    "directory",
    "binary",
    "max_files",
    "max_file_bytes",
]


class PayloadOmission(BaseModel):
    """Record of a candidate path that was not included in the payload."""

    model_config = {"extra": "forbid", "strict": True}

    path: str
    reason: OmissionReason


class PayloadFile(BaseModel):
    """Sandbox-relative source file content attached to a failure payload."""

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


class ResolvedAgentSettings(BaseModel):
    """Effective agent settings for one run drive; carries no credential values."""

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    provider: str
    model: str | None
    endpoint: str | None
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)


class AgentResponseStatus(StrEnum):
    """Normalized outcomes from an agent adapter call."""

    PROPOSED_PATCH = "proposed_patch"
    NO_OP = "no_op"
    UNFIXABLE = "unfixable"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"


class AgentRequest(BaseModel):
    """Input package for ``BaseAgentProvider.propose_fix``."""

    model_config = {"extra": "forbid", "strict": True}

    mode: Literal["direct", "fix_failure", "review_remediation"]
    instruction: str = Field(min_length=1)
    payload: AgentFailurePayload | None = None
    sandbox_path: Path
    timeout_seconds: int = Field(ge=1)
    model: str | None = None
    endpoint: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_files: int | None = None
    max_patch_kb: int | None = None
    reject_binary_changes: bool | None = None

    @model_validator(mode="after")
    def validate_mode_contract(self) -> AgentRequest:
        """Reject a blank instruction, a direct request with a payload, and a remediation request without one."""
        if not self.instruction.strip():
            raise ValueError("AgentRequest.instruction must not be blank.")

        if self.mode == "direct" and self.payload is not None:
            raise ValueError("AgentRequest mode 'direct' must not carry a failure payload.")

        if self.mode != "direct" and self.payload is None:
            raise ValueError(f"AgentRequest mode '{self.mode}' requires a failure payload.")

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
    mutation_baseline_ref: str | None = None

    @property
    def ok(self) -> bool:
        """Return True only when a patch was proposed."""
        return self.status == AgentResponseStatus.PROPOSED_PATCH


class ProviderKind(StrEnum):
    """How a provider applies its fix: by editing the sandbox directly or by returning a diff."""

    DIRECT_MUTATION = "direct_mutation"
    DIFF_RETURNING = "diff_returning"


@dataclass(frozen=True)
class AgentAttempt:
    """Classified result of one agent attempt before an adapter turns it into a caller-specific outcome."""

    status: AgentResponseStatus
    summary: str | None = None
    unfixable_reason: str | None = None
    touched_files: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """Return True for PROPOSED_PATCH and NO_OP."""
        return self.status in {AgentResponseStatus.PROPOSED_PATCH, AgentResponseStatus.NO_OP}
