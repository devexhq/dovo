"""Authored blueprint document definition models."""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from dovo.common.models import OnFailureSpec
from dovo.core.catalog.definitions.conditions import validate_condition_expression
from dovo.core.catalog.definitions.step import LoopStepBlock, StepDefinition
from dovo.core.catalog.exceptions import BlueprintValidationError
from dovo.core.inputs import ParameterInput

_SLUG_RE = re.compile(r"[^\w-]+")


def extract_defaults_on_failure(raw_defaults: object) -> object | None:
    """Return raw ``defaults.on_failure`` from a blueprint payload, if present."""
    if raw_defaults is None:
        return None
    if isinstance(raw_defaults, BlueprintDefaults):
        return raw_defaults.on_failure
    if isinstance(raw_defaults, dict):
        return raw_defaults.get("on_failure")
    return None


def apply_on_failure_default(
    step_data: dict[str, Any],
    on_failure_default: Any | None,
) -> dict[str, Any]:
    """Copy blueprint ``on_failure`` onto a step dict when the step omits it.

    Fill-if-omitted only: an explicit step ``on_failure`` is never merged or
    replaced. Loop blocks are left unchanged and handled separately.
    """
    if on_failure_default is None or "on_failure" in step_data:
        return step_data
    if step_data.get("type") == "loop":
        return step_data

    filled = dict(step_data)
    if isinstance(on_failure_default, OnFailureSpec):
        filled["on_failure"] = on_failure_default.model_dump(mode="json")
    else:
        filled["on_failure"] = copy.deepcopy(on_failure_default)
    return filled


def _validate_single_loop_block(loop: LoopStepBlock) -> None:
    """Validate condition expressions and referenced step IDs within a loop block."""
    known = {s.id for s in loop.do}
    for expr in loop.until:
        errors = validate_condition_expression(expr, known_step_ids=known)
        if errors:
            raise ValueError(f"Loop '{loop.id}': {'; '.join(errors)}")


def _validate_loop_steps(steps: list[StepDefinition | LoopStepBlock]) -> None:
    """Validate loop step blocks within a step definition list."""
    for step in steps:
        if isinstance(step, LoopStepBlock):
            _validate_single_loop_block(step)


class BlueprintDefaults(BaseModel):
    """Optional task/blueprint blueprint defaults applied fill-if-omitted to steps."""

    model_config = {"extra": "forbid"}
    on_failure: OnFailureSpec | None = None

    @field_validator("on_failure", mode="before")
    @classmethod
    def coerce_on_failure(cls, val: Any) -> Any:
        """Match StepDefinition.on_failure string-or-object coercion."""
        if val is None:
            return None
        return {"action": val} if isinstance(val, str) else val


class BlueprintDefinition(BaseModel):
    """Validated content for an executable catalog blueprint."""

    # Hand-authored YAML documents permit forward-compatible extra keys.
    model_config = {"extra": "ignore", "populate_by_name": True}

    name: str = Field(min_length=1)
    description: str = ""
    summary: str = ""
    version: int | str = 1
    use_worktree: bool = True
    timeout_seconds: int | None = Field(default=None, ge=1)
    env: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, ParameterInput] = Field(default_factory=dict)
    defaults: BlueprintDefaults = Field(default_factory=BlueprintDefaults)
    steps: list[StepDefinition | LoopStepBlock] = Field(default_factory=list)

    @classmethod
    def from_document(cls, raw: dict[str, object], *, key: str) -> BlueprintDefinition:
        """Validate a Catalog-loaded document, defaulting an omitted name to key."""
        if not isinstance(raw, dict):
            raise BlueprintValidationError("Blueprint document must be a mapping.")
        payload = dict(raw)
        if "name" not in payload:
            payload["name"] = key
        try:
            return cls.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            raise BlueprintValidationError(f"Blueprint definition validation failed: {exc}") from exc

    @field_validator("description", "summary", mode="before")
    @classmethod
    def _coerce_blank_text(cls, value: Any) -> Any:
        """Treat JSON/YAML null as an empty string."""
        return "" if value is None else value

    @model_validator(mode="before")
    @classmethod
    def _fill_step_shorthand_defaults(cls, data: Any) -> Any:
        """Fill missing step ids, map bare ``command:``, and inherit defaults.on_failure."""
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return payload

        on_failure_default = extract_defaults_on_failure(payload.get("defaults"))
        payload["steps"] = [
            cls._normalize_step_item(item, idx, on_failure_default) for idx, item in enumerate(raw_steps, start=1)
        ]
        return payload

    @model_validator(mode="after")
    def _validate_loops(self) -> BlueprintDefinition:
        """Validate loop step conditions reference known step ids."""
        _validate_loop_steps(self.steps)
        return self

    @classmethod
    def _ensure_step_id(cls, step_dict: dict[str, Any], idx: int) -> None:
        """Assign a default ``id`` when missing or empty."""
        existing = step_dict.get("id")
        if existing:
            return
        name_val = step_dict.get("name")
        if isinstance(name_val, str) and name_val.strip():
            slug = _SLUG_RE.sub("-", name_val.strip().lower()).strip("-")
            step_dict["id"] = slug or f"step-{idx}"
        else:
            step_dict["id"] = f"step-{idx}"

    @classmethod
    def _map_command_shorthand(cls, step_dict: dict[str, Any]) -> None:
        """Map bare ``command:`` shorthand onto ``run`` when no mode is set."""
        if "command" not in step_dict:
            return
        if any(key in step_dict for key in ("run", "uses", "type")):
            return
        step_dict["run"] = step_dict.pop("command")

    @classmethod
    def _normalize_step_item(cls, item: Any, idx: int, on_failure_default: Any | None = None) -> Any:
        """Normalize one raw step entry for ``StepDefinition`` validation."""
        if not isinstance(item, dict):
            return item
        step_dict = dict(item)
        cls._ensure_step_id(step_dict, idx)
        cls._map_command_shorthand(step_dict)
        return apply_on_failure_default(step_dict, on_failure_default)
