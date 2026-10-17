"""Authored step definition models."""

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from dovo.common.models import FailurePolicy, OnFailureSpec
from dovo.common.tool_policy import ToolPolicy, coerce_tool_policy
from dovo.core.catalog.exceptions import AskStepInvalidError

_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:/")
DEFAULT_STEP_TIMEOUT_SECONDS = 120


class StepType(StrEnum):
    """Supported step primitive types."""

    COMMAND = "command"
    AGENT = "agent"
    SCRIPT = "script"
    INTERNAL = "internal"
    ASK = "ask"


InteractionMode = Literal["confirm", "select", "multiselect", "text"]
CommentPolicy = Literal["disabled", "optional", "required"]

OPTION_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ASK_INCOMPATIBLE_FIELDS = (
    "run",
    "uses",
    "command",
    "prompt",
    "script_path",
    "tools",
    "artifacts",
    "assert",
    "max_files",
)


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
    if step.max_files is not None:
        conflicting.append("max_files")
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
    if step.max_files is not None and step.type != StepType.AGENT:
        raise ValueError(f"Step '{step.id}': 'max_files' is only valid with 'type: agent'.")


class InteractionOption(BaseModel):
    """One selectable answer of a confirm, select or multiselect prompt."""

    model_config = {"extra": "forbid", "strict": True}

    value: str
    label: str
    fail: bool = False
    comment: CommentPolicy | None = None


class InteractionComment(BaseModel):
    """Typed-comment policy and prompt text of an interaction."""

    model_config = {"extra": "forbid", "strict": True}

    policy: CommentPolicy = "disabled"
    message: str = "Comment"


class OptionsFrom(BaseModel):
    """Source of prompt options: items of a JSON array inside a published artifact file."""

    model_config = {"extra": "forbid", "strict": True}

    artifact: str = Field(min_length=1)
    file: str = Field(min_length=1)
    items_path: str = Field(min_length=1)
    value_field: str = Field(min_length=1)
    label_field: str = Field(min_length=1)


class InteractionSpec(BaseModel):
    """Authored `interaction:` block of a `type: ask` step."""

    model_config = {"extra": "forbid", "strict": True}

    mode: InteractionMode
    message: str = Field(min_length=1)
    artifact: str | None = None
    comment: InteractionComment = Field(default_factory=InteractionComment)
    options: list[InteractionOption] | None = None
    options_from: OptionsFrom | None = None


def interaction_spec_errors(spec: InteractionSpec) -> list[str]:
    """Return every static rule violation of an interaction block, without a step-id prefix."""
    if spec.mode == "text":
        return _text_mode_errors(spec)
    return [*_options_source_errors(spec), *_option_field_errors(spec), *_options_from_errors(spec)]


def _options_source_errors(spec: InteractionSpec) -> list[str]:
    """Return violations of the per-mode option count and options / options_from exclusivity rules."""
    if spec.mode == "confirm":
        return _confirm_source_errors(spec)
    options = spec.options
    if options is not None and spec.options_from is not None:
        return ["Specify either 'options' or 'options_from', not both."]
    if options is None and spec.options_from is None:
        return [f"'{spec.mode}' interaction requires 'options' or 'options_from'."]
    minimum = 2 if spec.mode == "select" else 1
    if options is not None and len(options) < minimum:
        return [f"'{spec.mode}' interaction needs at least {minimum} option(s), got {len(options)}."]
    return []


def _confirm_source_errors(spec: InteractionSpec) -> list[str]:
    """Return violations of the confirm rules: no options_from, and exactly 2 options when any are given."""
    if spec.options_from is not None:
        return ["'confirm' interaction cannot use 'options_from'."]
    if spec.options is not None and len(spec.options) != 2:
        return [f"'confirm' interaction needs exactly 2 options or none, got {len(spec.options)}."]
    return []


def _option_field_errors(spec: InteractionSpec) -> list[str]:
    """Return violations of option value pattern, uniqueness, label and `fail` placement."""
    errors: list[str] = []
    seen: set[str] = set()
    for option in spec.options or []:
        errors.extend(_single_option_errors(option, spec.mode))
        if option.value in seen:
            errors.append(f"Option value '{option.value}' appears more than once.")
        seen.add(option.value)
    return errors


def _single_option_errors(option: InteractionOption, mode: InteractionMode) -> list[str]:
    """Return violations of one option's value pattern, label and `fail` placement."""
    errors: list[str] = []
    if not OPTION_VALUE_RE.match(option.value):
        errors.append(f"Option value '{option.value}' must match ^[A-Za-z0-9_.-]{{1,64}}$.")
    if not option.label.strip():
        errors.append(f"Option '{option.value}' must have a non-empty label.")
    if option.fail and mode not in ("confirm", "select"):
        errors.append(f"Option '{option.value}' cannot set 'fail' in a '{mode}' interaction.")
    return errors


def _text_mode_errors(spec: InteractionSpec) -> list[str]:
    """Return violations of the `text` mode rules: no options, no options_from, no `comment`."""
    errors: list[str] = []
    if spec.options is not None:
        errors.append("'text' interaction cannot use 'options'.")
    if spec.options_from is not None:
        errors.append("'text' interaction cannot use 'options_from'.")
    if "comment" in spec.model_fields_set:
        errors.append("'text' interaction cannot use 'comment'.")
    return errors


def _options_from_errors(spec: InteractionSpec) -> list[str]:
    """Return a violation when `options_from.file` is empty, absolute or contains a `..` segment."""
    if spec.options_from is None:
        return []
    path = spec.options_from.file
    normalized = path.replace("\\", "/")
    unsafe = not normalized or normalized.startswith("/") or _DRIVE_PATH_RE.match(normalized)
    if unsafe or any(part == ".." for part in normalized.split("/")):
        return [f"options_from.file '{path}' must be a relative path without '..' segments."]
    return []


def _validate_ask_shape(step: "StepDefinition") -> None:
    """Raise AskStepInvalidError for ask-step field conflicts, a missing interaction, or any interaction rule violation."""
    if step.type != StepType.ASK:
        if step.interaction is not None:
            raise AskStepInvalidError(f"Step '{step.id}': 'interaction' is only valid with 'type: ask'.")
        return
    conflicting = [name for name in _ASK_INCOMPATIBLE_FIELDS if _ask_conflicts(step, name)]
    if conflicting:
        raise AskStepInvalidError(f"Step '{step.id}': 'type: ask' cannot be combined with {', '.join(conflicting)}.")
    if step.interaction is None:
        raise AskStepInvalidError(f"Step '{step.id}': 'type: ask' requires 'interaction'.")
    errors = interaction_spec_errors(step.interaction)
    if errors:
        raise AskStepInvalidError("; ".join(f"Step '{step.id}': {error}" for error in errors))


def _ask_conflicts(step: "StepDefinition", name: str) -> bool:
    """Return True when the field named `name` is set on the step."""
    value = step.assert_ if name == "assert" else getattr(step, name)
    return bool(value) if isinstance(value, (str, list)) else value is not None


class ArtifactPublishSpec(BaseModel):
    """Declarative artifact publish spec attached to a step, applied on step success."""

    model_config = {"extra": "forbid", "strict": True}

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    retention_days: int | None = Field(default=None, ge=0)
    required: bool = True


class StepDefinition(BaseModel):
    """Single model for a step in a blueprint or in the step catalog."""

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
    interaction: InteractionSpec | None = None
    max_files: int | None = Field(default=None, ge=1)

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, val: object) -> object:
        """Coerce YAML/JSON strings to StepType (strict mode skips enum coercion)."""
        if isinstance(val, str):
            try:
                return StepType(val)
            except ValueError:
                pass
        return val

    @field_validator("tools", mode="before")
    @classmethod
    def reject_legacy_tools(cls, val: object) -> object:
        """Reject the legacy string list with migration guidance."""
        return coerce_tool_policy(val)

    @field_validator("on_failure", mode="before")
    @classmethod
    def coerce_on_failure(cls, val: object) -> object:
        """Accept bare 'abort' string or full {action, max_retries, backoff_ms, on_max_retries} object."""
        if val is None:
            return None
        return {"action": val} if isinstance(val, str) else val

    @model_validator(mode="after")
    def validate_step_shape(self) -> "StepDefinition":
        """Enforce exactly one of run/uses/inline-type mode."""
        _validate_ask_shape(self)
        if self.type == StepType.ASK:
            return self
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
    def coerce_on_max_iterations(cls, val: object) -> object:
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
