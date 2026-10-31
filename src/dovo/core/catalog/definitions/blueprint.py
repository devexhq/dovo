"""Authored blueprint document definition models."""

from __future__ import annotations

import copy
import re
from typing import Annotated, Any

from pydantic import BaseModel, Discriminator, Field, Tag, ValidationError, field_validator, model_validator

from dovo.common.models import OnFailureSpec
from dovo.core.catalog.definitions.conditions import ConditionScope, validate_condition_expression
from dovo.core.catalog.definitions.step import BlueprintStep, IfStepBlock, LoopStepBlock, StepDefinition
from dovo.core.catalog.exceptions import BlueprintValidationError
from dovo.core.inputs import ParameterInput

_SLUG_RE = re.compile(r"[^\w-]+")
_BLOCK_TYPES = ("loop", "if")
_EARLIER_STEPS = "earlier steps"


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
    on_failure_default: object | None,
) -> dict[str, Any]:
    """Copy blueprint ``on_failure`` onto a step dict when the step omits it.

    Fill-if-omitted only: an explicit step ``on_failure`` is never merged or
    replaced. Loop and if blocks are left unchanged; their steps are filled one by one.
    """
    if on_failure_default is None or "on_failure" in step_data:
        return step_data
    if step_data.get("type") in _BLOCK_TYPES:
        return step_data

    filled = dict(step_data)
    if isinstance(on_failure_default, OnFailureSpec):
        filled["on_failure"] = on_failure_default.model_dump(mode="json")
    else:
        filled["on_failure"] = copy.deepcopy(on_failure_default)
    return filled


def _step_kind(value: object) -> str:
    """Return the union tag of one steps entry: 'loop' or 'if' for a block, 'step' for anything else."""
    kind = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    return kind if kind in _BLOCK_TYPES else "step"


_StepEntry = Annotated[
    Annotated[StepDefinition, Tag("step")] | Annotated[LoopStepBlock, Tag("loop")] | Annotated[IfStepBlock, Tag("if")],
    Discriminator(_step_kind),
]


def _guard_errors(owner: str, expressions: list[str] | None, scope: ConditionScope) -> list[str]:
    """Return every error of a 'when' or 'condition' list, each prefixed with the step or block that owns it."""
    return [
        f"{owner}: {error}"
        for expression in expressions or []
        for error in validate_condition_expression(expression, scope=scope)
    ]


def _when_scope(known: frozenset[str], *, in_loop: bool) -> ConditionScope:
    """Return the scope of a 'when' list that may reference the known step ids."""
    return ConditionScope(clause="when", known_step_ids=known, scope_label=_EARLIER_STEPS, allow_iteration=in_loop)


def _sequence_errors(steps: list[StepDefinition], outer: frozenset[str], *, in_loop: bool) -> list[str]:
    """Return the 'when' errors of a step list whose steps may reference outer ids and the steps before them."""
    errors: list[str] = []
    seen: set[str] = set()
    for step in steps:
        errors.extend(_guard_errors(f"Step '{step.id}'", step.when, _when_scope(outer | seen, in_loop=in_loop)))
        seen.add(step.id)
    return errors


def _loop_errors(loop: LoopStepBlock, outer: frozenset[str]) -> list[str]:
    """Return the until, loop 'when' and 'do' step 'when' errors of one loop block."""
    known = {step.id for step in loop.do}
    errors = [
        f"Loop '{loop.id}': {error}"
        for expression in loop.until
        for error in validate_condition_expression(expression, known_step_ids=known)
    ]
    errors.extend(_guard_errors(f"Loop '{loop.id}'", loop.when, _when_scope(outer, in_loop=False)))
    errors.extend(_sequence_errors(loop.do, outer, in_loop=True))
    return errors


def _if_errors(block: IfStepBlock, outer: frozenset[str]) -> list[str]:
    """Return the condition errors of one if block and the 'when' errors of each branch's steps."""
    scope = ConditionScope(clause="if", known_step_ids=outer, scope_label=_EARLIER_STEPS, allow_iteration=False)
    return [
        *_guard_errors(f"If block '{block.id}'", block.condition, scope),
        *_sequence_errors(block.branch("then"), outer, in_loop=False),
        *_sequence_errors(block.branch("else"), outer, in_loop=False),
    ]


def _condition_errors(steps: list[BlueprintStep]) -> list[str]:
    """Return every condition error in steps: each expression may reference only earlier steps of its own or an enclosing scope."""
    errors: list[str] = []
    known: set[str] = set()
    for step in steps:
        outer = frozenset(known)
        if isinstance(step, LoopStepBlock):
            errors.extend(_loop_errors(step, outer))
        elif isinstance(step, IfStepBlock):
            errors.extend(_if_errors(step, outer))
            known.update(branch_step.id for branch_step in [*step.branch("then"), *step.branch("else")])
        else:
            errors.extend(_guard_errors(f"Step '{step.id}'", step.when, _when_scope(outer, in_loop=False)))
            known.add(step.id)
    return errors


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
    steps: list[_StepEntry] = Field(default_factory=list)

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
    def _coerce_blank_text(cls, value: object) -> object:
        """Treat JSON/YAML null as an empty string."""
        return "" if value is None else value

    @model_validator(mode="before")
    @classmethod
    def _fill_step_shorthand_defaults(cls, data: object) -> object:
        """Fill missing step ids, map bare ``command:``, and inherit defaults.on_failure, inside blocks too."""
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return payload

        on_failure_default = extract_defaults_on_failure(payload.get("defaults"))
        payload["steps"] = [
            cls._normalize_entry(item, f"step-{idx}", on_failure_default) for idx, item in enumerate(raw_steps, start=1)
        ]
        return payload

    @model_validator(mode="after")
    def _validate_conditions(self) -> BlueprintDefinition:
        """Validate every until, when and condition expression against the steps it may reference."""
        errors = _condition_errors(self.steps)
        if errors:
            raise ValueError(" ".join(errors))
        return self

    @classmethod
    def _ensure_step_id(cls, step_dict: dict[str, Any], fallback: str) -> None:
        """Assign the slug of ``name``, else fallback, as ``id`` when it is missing or empty."""
        if step_dict.get("id"):
            return
        name_val = step_dict.get("name")
        slug = _SLUG_RE.sub("-", name_val.strip().lower()).strip("-") if isinstance(name_val, str) else ""
        step_dict["id"] = slug or fallback

    @classmethod
    def _map_command_shorthand(cls, step_dict: dict[str, Any]) -> None:
        """Map bare ``command:`` shorthand onto ``run`` when no mode is set."""
        if "command" not in step_dict:
            return
        if any(key in step_dict for key in ("run", "uses", "type")):
            return
        step_dict["run"] = step_dict.pop("command")

    @classmethod
    def _normalize_entry(cls, item: object, fallback_id: str, on_failure_default: object | None) -> object:
        """Normalize one raw top-level entry: a step, or a block whose own steps are normalized as well."""
        if not isinstance(item, dict):
            return item
        entry = dict(item)
        cls._ensure_step_id(entry, fallback_id)
        if entry.get("type") == "loop":
            cls._normalize_block_list(entry, "do", f"{entry['id']}-step", on_failure_default)
        elif entry.get("type") == "if":
            cls._normalize_block_list(entry, "then", f"{entry['id']}-then-step", on_failure_default)
            cls._normalize_block_list(entry, "else", f"{entry['id']}-else-step", on_failure_default)
        else:
            cls._map_command_shorthand(entry)
        return apply_on_failure_default(entry, on_failure_default)

    @classmethod
    def _normalize_block_list(
        cls, block: dict[str, object], list_name: str, id_prefix: str, on_failure_default: object | None
    ) -> None:
        """Normalize each step of block[list_name], deriving a missing id as '<id_prefix>-<n>'."""
        raw_steps = block.get(list_name)
        if not isinstance(raw_steps, list):
            return
        block[list_name] = [
            item
            if isinstance(item, dict) and item.get("type") in _BLOCK_TYPES
            # A nested block is left as written so the block's own validator names it.
            else cls._normalize_entry(item, f"{id_prefix}-{position}", on_failure_default)
            for position, item in enumerate(raw_steps, start=1)
        ]
