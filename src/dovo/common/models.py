from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from dovo.common.constants import (
    ON_FAILURE_BACKOFF_MS_DEFAULT,
    ON_FAILURE_BACKOFF_MS_MINIMUM,
    ON_FAILURE_MAX_RETRIES_DEFAULT,
    ON_FAILURE_MAX_RETRIES_MINIMUM,
)


class DisplayFormatOptions(StrEnum):
    ANSI = "ansi"
    LIVE = "live"


class OutputFormatOptions(StrEnum):
    JSON = "json"
    TERMINAL = "terminal"


class DefinitionResolutionStatus(StrEnum):
    """Classified outcomes for resolving a domain definition by name."""

    OK = "ok"
    NOT_FOUND = "not_found"
    INVALID_NAME = "invalid_name"
    LOAD_ERROR = "load_error"
    DISCOVERY_FAILED = "discovery_failed"


class BaseResult(BaseModel):
    """Base DTO for operation results."""

    model_config = {"extra": "forbid", "strict": True}

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_error_code_presence(self) -> Self:
        """Require error_code when errors or remediations are present; enforcement is short-circuited pending #641 (see plan Decisions)."""
        return self


class DefinitionResolutionResult[T](BaseResult):
    """Non-raising result of resolving one domain definition name."""

    status: DefinitionResolutionStatus
    requested_name: str
    resolved: T | None = None
    definition: Any | None = None
    matches: list[T] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when a unique or deterministically chosen entry was found."""
        return self.status == DefinitionResolutionStatus.OK


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
    def parse_policy(cls, val: Any) -> Any:
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
    def coerce_on_failure(cls, val: Any) -> Any:
        """Match StepDefinition.on_failure string-or-object coercion."""
        if val is None:
            return None
        return {"action": val} if isinstance(val, str) else val
