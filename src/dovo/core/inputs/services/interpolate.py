"""Template variable interpolation for resolved blueprint inputs and execution metadata."""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\$?\{\{\s*([^}]+?)\s*\}\}")
_INTERPOLATED_FIELDS = ("command", "prompt", "script_path", "run")
_STEP_ENTRY_FIELDS = {"id", "name", "index", "status", "exit_code"}
_STEPS_BRACKET_RE = re.compile(r"^steps\s*\[\s*(['\"]?)(.*?)\1\s*\]\s*\.\s*([A-Za-z0-9_]+)$")
_STEPS_OUTPUTS_BRACKET_RE = re.compile(r"^steps\s*\[\s*(['\"])(.*?)\1\s*\]\s*\.\s*outputs\s*\.\s*(.+)$")


def _extract_iteration_index(meta: Any) -> str:
    """Extract the current iteration index from metadata or default to '1'."""
    iteration = getattr(meta, "iteration", None)
    if iteration is not None:
        idx = getattr(iteration, "index", None)
        if idx is not None:
            return str(idx)
    return "1"


_METADATA_EXTRACTORS: dict[str, Any] = {
    "step.id": lambda meta: str(meta.step.id),
    "step.name": lambda meta: str(meta.step.name),
    "step.index": lambda meta: str(meta.step.index),
    "step.attempt": lambda meta: str(meta.step.attempt),
    "blueprint.name": lambda meta: str(meta.blueprint.name),
    "blueprint.key": lambda meta: str(meta.blueprint.key),
    "blueprint.sha": lambda meta: str(meta.blueprint.key),
    "task.name": lambda meta: str(meta.blueprint.name),
    "task.sha": lambda meta: str(meta.blueprint.key),
    "workflow.name": lambda meta: str(meta.blueprint.name),
    "workflow.sha": lambda meta: str(meta.blueprint.key),
    "previous_step.id": lambda meta: str(meta.previous_step.id),
    "previous_step.name": lambda meta: str(meta.previous_step.name),
    "previous_step.index": lambda meta: str(meta.previous_step.index),
    "previous_step.status": lambda meta: str(meta.previous_step.status),
    "previous_step.exit_code": lambda meta: str(meta.previous_step.exit_code),
    "iteration.index": _extract_iteration_index,
    "iteration.attempt": _extract_iteration_index,
}


def _parse_steps_selector(key: str) -> tuple[str, str, bool] | None:
    """Parse a steps expression into (selector, field_name, is_quoted)."""
    bracket_match = _STEPS_BRACKET_RE.match(key)
    if bracket_match:
        quote = bracket_match.group(1)
        selector = bracket_match.group(2).strip()
        field_name = bracket_match.group(3)
        return selector, field_name, bool(quote)

    if key.startswith("steps."):
        parts = key.split(".")
        if len(parts) >= 3:
            field_name = parts[-1]
            selector = ".".join(parts[1:-1])
            return selector, field_name, False

    return None


def _resolve_step_entry_field(entry: Any | None, field_name: str) -> str:
    """Extract named attribute from step entry as a string."""
    if entry is None:
        return ""
    value = getattr(entry, field_name, "")
    return str(value) if value is not None else ""


def _find_step_by_id(steps: list[Any], step_id: str) -> Any | None:
    """Find the most recent step matching step_id in steps list."""
    for entry in reversed(steps):
        if getattr(entry, "id", None) == step_id:
            return entry
    return None


def _resolve_steps_placeholder(key: str, metadata: Any) -> tuple[bool, str]:
    """Resolve a steps placeholder expression against step execution metadata."""
    parsed = _parse_steps_selector(key)
    if parsed is None:
        return False, ""

    selector, field_name, is_quoted = parsed
    if field_name not in _STEP_ENTRY_FIELDS:
        return False, ""

    steps_list = getattr(metadata, "steps", None) or []

    if not is_quoted:
        try:
            index = int(selector)
            if 0 <= index < len(steps_list) or (index < 0 and abs(index) <= len(steps_list)):
                entry = steps_list[index]
                return True, _resolve_step_entry_field(entry, field_name)
            return True, ""
        except ValueError:
            pass

    entry = _find_step_by_id(steps_list, selector)
    return True, _resolve_step_entry_field(entry, field_name)


def _parse_steps_outputs_selector(key: str) -> tuple[str, str] | None:
    """Parse `steps.<id>.outputs.<key>` or `steps['<id>'].outputs.<key>` into (step_id, output_key)."""
    bracket_match = _STEPS_OUTPUTS_BRACKET_RE.match(key)
    if bracket_match:
        return bracket_match.group(2), bracket_match.group(3)
    if key.startswith("steps.") and ".outputs." in key:
        remainder = key[len("steps.") :]
        step_id, _, output_key = remainder.partition(".outputs.")
        if step_id and output_key:
            return step_id, output_key
    return None


def _resolve_step_outputs_placeholder(key: str, metadata: Any) -> tuple[bool, str]:
    """Resolve steps.<id>.outputs.<key> to the step's output value, or "" when the step or key is unknown."""
    parsed = _parse_steps_outputs_selector(key)
    if parsed is None:
        return False, ""
    step_id, output_key = parsed
    steps_list = getattr(metadata, "steps", None) or []
    entry = _find_step_by_id(steps_list, step_id)
    outputs = getattr(entry, "outputs", None) if entry is not None else None
    return True, str((outputs or {}).get(output_key, ""))


def _resolve_metadata_placeholder(key: str, metadata: Any) -> tuple[bool, str]:
    """Extract a placeholder value from execution metadata if matched."""
    extractor = _METADATA_EXTRACTORS.get(key)
    if extractor is not None:
        try:
            return True, extractor(metadata)
        except AttributeError:
            pass
    if key.startswith("steps[") or key.startswith("steps."):
        found, value = _resolve_step_outputs_placeholder(key, metadata)
        if found:
            return True, value
        return _resolve_steps_placeholder(key, metadata)
    return False, ""


def _resolve_input_placeholder(key: str, inputs: dict[str, Any]) -> tuple[bool, str]:
    """Extract a placeholder value from inputs dictionary if present."""
    if key.startswith("inputs."):
        input_name = key[7:]
        if input_name in inputs:
            return True, str(inputs[input_name])
    if key in inputs:
        return True, str(inputs[key])
    return False, ""


def _resolve_placeholder(
    key: str,
    inputs: dict[str, Any] | None,
    metadata: Any | None,
) -> tuple[bool, str]:
    """Resolve placeholder expression by querying metadata first, then inputs."""
    if metadata is not None:
        found, val = _resolve_metadata_placeholder(key, metadata)
        if found:
            return True, val
    if inputs is not None:
        found, val = _resolve_input_placeholder(key, inputs)
        if found:
            return True, val
    return False, ""


def interpolate_string(
    template: str,
    inputs: dict[str, Any] | None = None,
    metadata: Any | None = None,
) -> str:
    """Replace ``${{ ... }}`` and ``{{ ... }}`` placeholders with resolved string values."""
    if not inputs and metadata is None:
        return template

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        found, value = _resolve_placeholder(key, inputs, metadata)
        if not found:
            return match.group(0)
        return value

    return _PLACEHOLDER_RE.sub(_replace, template)


def _interpolated_field_updates(
    step: Any,
    inputs: dict[str, Any] | None = None,
    metadata: Any | None = None,
) -> dict[str, Any]:
    """Compute string field replacements for an executable step model."""
    updates: dict[str, Any] = {}
    for field_name in _INTERPOLATED_FIELDS:
        value = getattr(step, field_name, None)
        if isinstance(value, str) and value:
            interpolated = interpolate_string(value, inputs=inputs, metadata=metadata)
            if interpolated != value:
                updates[field_name] = interpolated
    return updates


def _interpolated_env(
    step: Any,
    inputs: dict[str, Any] | None = None,
    metadata: Any | None = None,
) -> dict[str, str] | None:
    """Compute environment dictionary replacements for an executable step model."""
    env = getattr(step, "env", None)
    if not isinstance(env, dict) or not env:
        return None
    new_env = {
        key: interpolate_string(value, inputs=inputs, metadata=metadata) if isinstance(value, str) else value
        for key, value in env.items()
    }
    return new_env if new_env != env else None


def interpolate_step_fields(
    step: Any,
    inputs: dict[str, Any] | None = None,
    metadata: Any | None = None,
) -> Any:
    """Return a copy of ``step`` with interpolatable string fields substituted.

    Supports ``command``, ``prompt``, ``script_path``, ``run``, and string values
    inside ``env``. Unknown attributes are left untouched.
    """
    if not inputs and metadata is None:
        return step

    updates = _interpolated_field_updates(step, inputs=inputs, metadata=metadata)
    new_env = _interpolated_env(step, inputs=inputs, metadata=metadata)
    if new_env is not None:
        updates["env"] = new_env
    if not updates:
        return step
    return step.model_copy(update=updates)
