"""Authored step definition models."""

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from dovo.common.models import FailurePolicy, OnFailureSpec
from dovo.common.tool_policy import ToolPolicy, coerce_tool_policy

_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:/")
DEFAULT_STEP_TIMEOUT_SECONDS = 120


class StepType(StrEnum):
    """Supported step primitive types."""

    COMMAND = "command"
    AGENT = "agent"
    SCRIPT = "script"
    INTERNAL = "internal"


class StepAssert(BaseModel):
    """Declarative verification criteria for standard steps."""

    model_config = {"extra": "forbid", "strict": True}

    exit_code: int | list[int] | None = None
    output_contains: str | list[str] | None = None
    output_not_contains: str | list[str] | None = None
    regex_match: str | None = None
    json_match: dict[str, Any] | None = None
    file_exists: str | list[str] | None = None
    file_not_exists: str | list[str] | None = None
    file_not_empty: str | list[str] | None = None

    @model_validator(mode="after")
    def validate_file_assert_paths(self) -> "StepAssert":
        """Reject absolute paths and parent-directory traversal in file asserts."""
        self._validate_assert_paths(self.file_exists, "file_exists")
        self._validate_assert_paths(self.file_not_exists, "file_not_exists")
        self._validate_assert_paths(self.file_not_empty, "file_not_empty")
        return self

    def _validate_assert_paths(self, value: str | list[str] | None, field_name: str) -> None:
        """Reject empty, absolute, or parent-traversal paths in file-system asserts."""
        if value is None:
            return
        entries = [value] if isinstance(value, str) else value
        for entry in entries:
            if self._is_unsafe_assert_path(entry):
                raise ValueError(
                    f"{field_name} path must be a non-empty relative path without '..' segments: {entry!r}"
                )

    def _is_unsafe_assert_path(self, path: str) -> bool:
        """Return True when ``path`` is absolute, empty, or contains a ``..`` segment."""
        normalized = path.replace("\\", "/")
        if not normalized or normalized.startswith("/") or _DRIVE_PATH_RE.match(normalized):
            return True
        return any(part == ".." for part in normalized.split("/"))


def _validate_run_shape(step: "StepDefinition") -> None:
    """Reject 'run' combined with any uses/inline-type-mode-only fields."""
    run_incompatible = ("uses", "command", "type", "prompt", "script_path")
    conflicting = [f for f in run_incompatible if getattr(step, f)]
    if step.tools is not None:
        conflicting.append("tools")
    if conflicting:
        raise ValueError(f"Step '{step.id}': 'run' cannot be combined with {', '.join(conflicting)}.")


def _validate_inline_type_shape(step: "StepDefinition") -> None:
    """Require the field matching the step's inline 'type'."""
    if step.type == StepType.COMMAND and not step.command:
        raise ValueError("Command steps must specify a non-empty 'command' string.")
    if step.type == StepType.AGENT and not step.prompt:
        raise ValueError("Agent steps must specify a non-empty 'prompt' string.")
    if step.type == StepType.SCRIPT and not step.script_path:
        raise ValueError("Script steps must specify a non-empty 'script_path' string.")
    if step.type == StepType.INTERNAL and not step.command:
        raise ValueError("Internal steps must specify a non-empty 'command' string naming the internal command to run.")


class ArtifactPublishSpec(BaseModel):
    """Declarative artifact publish spec attached to a step, applied on step success."""

    model_config = {"extra": "forbid", "strict": True}

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    retention_days: int | None = Field(default=None, ge=0)


class StepDefinition(BaseModel):
    """Single model for catalog step blueprints, workflow steps, and task steps."""

    model_config = {"extra": "forbid", "strict": True, "populate_by_name": True}

    id: str
    uses: str | None = None
    run: str | None = None
    name: str | None = None
    type: StepType | None = None
    description: str | None = None
    command: str | None = None
    prompt: str | None = None
    script_path: str | None = None
    tools: ToolPolicy | None = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=DEFAULT_STEP_TIMEOUT_SECONDS, gt=0)
    assert_: StepAssert | None = Field(default=None, validation_alias="assert", serialization_alias="assert")
    on_failure: OnFailureSpec = Field(default_factory=lambda: OnFailureSpec(action=FailurePolicy.ABORT))
    artifacts: list[ArtifactPublishSpec] = Field(default_factory=list)

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, val: Any) -> Any:
        """Coerce YAML/JSON strings to StepType (strict mode skips enum coercion)."""
        if isinstance(val, str):
            try:
                return StepType(val)
            except ValueError:
                pass
        return val

    @field_validator("tools", mode="before")
    @classmethod
    def reject_legacy_tools(cls, val: Any) -> Any:
        """Reject the legacy string list with migration guidance."""
        return coerce_tool_policy(val)

    @field_validator("on_failure", mode="before")
    @classmethod
    def coerce_on_failure(cls, val: Any) -> Any:
        """Accept bare 'abort' string or full {action, max_retries, backoff_ms, on_max_retries} object."""
        if val is None:
            return None
        return {"action": val} if isinstance(val, str) else val

    @model_validator(mode="after")
    def validate_step_shape(self) -> "StepDefinition":
        """Enforce exactly one of run/uses/inline-type mode."""
        if self.run is not None:
            _validate_run_shape(self)
        elif self.uses is not None:
            pass  # resolved to a concrete step at load/execution time
        elif self.type is not None:
            _validate_inline_type_shape(self)
        else:
            raise ValueError(f"Step '{self.id}' must specify one of 'run', 'uses', or 'type'.")
        return self


class LoopStepBlock(BaseModel):
    """Loop block step execution definition."""

    # Hand-authored YAML loop blocks permit forward-compatible extra keys.
    model_config = {"extra": "ignore", "populate_by_name": True}

    id: str = Field(min_length=1)
    type: Literal["loop"]
    max_iterations: int = Field(default=5, ge=1)
    until: list[str] = Field(min_length=1)
    do: list[StepDefinition] = Field(min_length=1)
    on_max_iterations: FailurePolicy = FailurePolicy.PROMPT_USER

    @field_validator("on_max_iterations", mode="before")
    @classmethod
    def coerce_on_max_iterations(cls, val: Any) -> Any:
        """Coerce string value to FailurePolicy enum instance."""
        if isinstance(val, str):
            try:
                return FailurePolicy(val)
            except ValueError:
                pass
        return val

    @model_validator(mode="after")
    def validate_on_max_iterations_context(self) -> "LoopStepBlock":
        """Reject FailurePolicy values not valid for the terminal context (e.g. RETRY)."""
        allowed = FailurePolicy.context("terminal")
        if self.on_max_iterations not in allowed:
            raise ValueError(f"Loop '{self.id}': on_max_iterations must be one of {sorted(allowed)}.")
        return self
