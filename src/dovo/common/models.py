from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from dovo.common.constants import (
    ON_FAILURE_BACKOFF_MS_DEFAULT,
    ON_FAILURE_BACKOFF_MS_MINIMUM,
    ON_FAILURE_MAX_RETRIES_DEFAULT,
    ON_FAILURE_MAX_RETRIES_MINIMUM,
)
from dovo.common.diagnostic_log.models import DiagnosticRecord


class DisplayFormatOptions(StrEnum):
    ANSI = "ansi"
    LIVE = "live"


class OutputFormatOptions(StrEnum):
    JSON = "json"
    TERMINAL = "terminal"


class AgentEnvModeOptions(StrEnum):
    """Allowed values for the --env-mode flag."""

    ALLOWLIST = "allowlist"
    INHERIT = "inherit"


class Remediation(BaseModel):
    """One recovery step for a failed or warning result: what to do, and the command that does it when there is one."""

    model_config = {"extra": "forbid", "strict": True}

    message: str = Field(min_length=1)
    command: str | None = Field(default=None, min_length=1)


class BaseResult(BaseModel):
    """Base DTO for operation results."""

    model_config = {"extra": "forbid", "strict": True}

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    error_code: str | None = None
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)  # error-level records only

    @model_validator(mode="after")
    def validate_error_code_presence(self) -> Self:
        """Require error_code when errors or remediations are present; enforcement is not enabled yet."""
        return self


class FailurePolicy(StrEnum):
    """Canonical failure-handling vocabulary shared by steps and loop blocks."""

    ABORT = "abort"
    CONTINUE = "continue"
    PROMPT_USER = "prompt_user"
    RETRY = "retry"

    @classmethod
    def context(cls, name: str) -> frozenset["FailurePolicy"]:
        """Return the allowed FailurePolicy subset for a given usage context."""
        if name == "terminal":
            return frozenset({cls.ABORT, cls.CONTINUE, cls.PROMPT_USER})
        return frozenset(cls)


class OnFailureSpec(BaseModel):
    """Normalized/default on_failure directive: action, retry tuning, and post-retry escalation."""

    model_config = {"extra": "forbid", "strict": True}

    action: FailurePolicy
    max_retries: int = Field(default=ON_FAILURE_MAX_RETRIES_DEFAULT, ge=ON_FAILURE_MAX_RETRIES_MINIMUM)
    backoff_ms: int = Field(default=ON_FAILURE_BACKOFF_MS_DEFAULT, ge=ON_FAILURE_BACKOFF_MS_MINIMUM)
    on_max_retries: FailurePolicy = FailurePolicy.ABORT

    @field_validator("action", "on_max_retries", mode="before")
    @classmethod
    def parse_policy(cls, val: object) -> object:
        """Coerce string values to FailurePolicy enum instances."""
        if isinstance(val, str):
            try:
                return FailurePolicy(val)
            except ValueError:
                pass
        return val

    @model_validator(mode="after")
    def validate_on_max_retries_context(self) -> "OnFailureSpec":
        """on_max_retries must be terminal (no RETRY-on-RETRY-exhaustion)."""
        allowed = FailurePolicy.context("terminal")
        if self.on_max_retries not in allowed:
            raise ValueError(f"on_max_retries must be one of {sorted(allowed)}, got {self.on_max_retries!r}.")
        return self


class BlueprintDefaults(BaseModel):
    """Optional blueprint defaults applied fill-if-omitted to steps."""

    model_config = {"extra": "forbid"}
    on_failure: OnFailureSpec | None = None

    @field_validator("on_failure", mode="before")
    @classmethod
    def coerce_on_failure(cls, val: object) -> object:
        """Match StepDefinition.on_failure string-or-object coercion."""
        if val is None:
            return None
        return {"action": val} if isinstance(val, str) else val
