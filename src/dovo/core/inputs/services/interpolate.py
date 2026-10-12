"""Template variable interpolation for resolved blueprint inputs and execution metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

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


class ExecutionMetadataView(Protocol):
    """Read-only attributes of execution metadata that placeholder providers consume."""

    @property
    def step(self) -> object:
        """Metadata step namespace."""
        ...

    @property
    def blueprint(self) -> object:
        """Metadata blueprint namespace."""
        ...

    @property
    def previous_step(self) -> object:
        """Metadata previous step namespace."""
        ...

    @property
    def iteration(self) -> object:
        """Metadata iteration namespace."""
        ...

    @property
    def steps(self) -> Sequence[object]:
        """Metadata steps namespace."""
        ...


class NamespaceProvider(Protocol):
    """Resolves placeholder expressions for one namespace."""

    namespace: str

    def resolve(self, path: str) -> str | None:
        """Return the rendered value for the dotted path, or None when this provider does not define it."""
        ...


class InputsProvider:
    """Resolves `inputs.<name>` and bare `<name>` references against the resolved blueprint inputs."""

    namespace = "inputs"

    def __init__(self, inputs: Mapping[str, Any]) -> None:
        self._inputs = inputs

    def resolve(self, path: str) -> str | None:
        """Return str(value) for `inputs.<name>` or a bare `<name>` present in inputs, else None."""
        if path.startswith("inputs."):
            input_name = path[len("inputs.") :]
            if input_name in self._inputs:
                return str(self._inputs[input_name])
        if path in self._inputs:
            return str(self._inputs[path])
        return None


class StepsProvider:
    """Resolves `steps[...]` and `steps.<id>.outputs.<key>` references against execution metadata."""

    namespace = "steps"

    def __init__(self, metadata: ExecutionMetadataView) -> None:
        self._steps: list[object] = list(metadata.steps or [])

    def resolve(self, path: str) -> str | None:
        """Return the step field or output value, '' for an unknown step/key, or None for a non-steps expression."""
        if not path.startswith(("steps[", "steps.")):
            return None

        outputs_selector = _parse_steps_outputs_selector(path)
        if outputs_selector is not None:
            step_id, output_key = outputs_selector
            entry = _find_step_by_id(self._steps, step_id)
            outputs = getattr(entry, "outputs", None) if entry is not None else None
            return str((outputs or {}).get(output_key, ""))

        return self._resolve_entry_field(path)

    def _resolve_entry_field(self, path: str) -> str | None:
        """Resolve `steps[<selector>].<field>` by index or step id; None when the expression is not a step field."""
        parsed = _parse_steps_selector(path)
        if parsed is None:
            return None

        selector, field_name, is_quoted = parsed
        if field_name not in _STEP_ENTRY_FIELDS:
            return None

        if not is_quoted:
            try:
                index = int(selector)
            except ValueError:
                pass
            else:
                if -len(self._steps) <= index < len(self._steps):
                    return _resolve_step_entry_field(self._steps[index], field_name)
                return ""

        return _resolve_step_entry_field(_find_step_by_id(self._steps, selector), field_name)


class MetadataProvider:
    """Resolves one execution-metadata namespace such as `step`, `blueprint` or `previous_step`."""

    def __init__(self, namespace: str, metadata: ExecutionMetadataView) -> None:
        self.namespace = namespace
        self._metadata = metadata

    def resolve(self, path: str) -> str | None:
        """Return the extractor value for a `<namespace>.<field>` key, or None when undefined."""
        if not path.startswith(f"{self.namespace}."):
            return None

        extractor = _METADATA_EXTRACTORS.get(path)
        if extractor is None:
            return None

        try:
            return extractor(self._metadata)
        except AttributeError:
            return None


def metadata_providers(metadata: ExecutionMetadataView) -> list[NamespaceProvider]:
    """Build one MetadataProvider per metadata namespace plus the StepsProvider."""
    namespaces = dict.fromkeys(key.partition(".")[0] for key in _METADATA_EXTRACTORS)
    providers: list[NamespaceProvider] = [MetadataProvider(namespace, metadata) for namespace in namespaces]
    providers.append(StepsProvider(metadata))
    return providers


def build_providers(
    *,
    inputs: Mapping[str, Any] | None = None,
    metadata: ExecutionMetadataView | None = None,
    artifacts: NamespaceProvider | None = None,
) -> list[NamespaceProvider]:
    """Return providers in resolution order: metadata, steps, artifacts, then inputs; omit those not supplied."""
    providers: list[NamespaceProvider] = []
    if metadata is not None:
        providers.extend(metadata_providers(metadata))
    if artifacts is not None:
        providers.append(artifacts)
    if inputs:
        providers.append(InputsProvider(inputs))
    return providers


def interpolate_string(template: str, providers: Sequence[NamespaceProvider]) -> str:
    """Replace ``${{ ... }}`` and ``{{ ... }}`` placeholders using the first provider that defines each path."""
    if not providers:
        return template

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        for provider in providers:
            value = provider.resolve(key)
            if value is not None:
                return value
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, template)


def _interpolated_field_updates(step: Any, providers: Sequence[NamespaceProvider]) -> dict[str, Any]:
    """Compute string field replacements for an executable step model."""
    updates: dict[str, Any] = {}
    for field_name in _INTERPOLATED_FIELDS:
        value = getattr(step, field_name, None)
        if isinstance(value, str) and value:
            interpolated = interpolate_string(value, providers)
            if interpolated != value:
                updates[field_name] = interpolated
    return updates


def _interpolated_env(step: Any, providers: Sequence[NamespaceProvider]) -> dict[str, str] | None:
    """Compute environment dictionary replacements for an executable step model."""
    env = getattr(step, "env", None)
    if not isinstance(env, dict) or not env:
        return None
    new_env = {
        key: interpolate_string(value, providers) if isinstance(value, str) else value for key, value in env.items()
    }
    return new_env if new_env != env else None


def interpolate_step_fields(step: Any, providers: Sequence[NamespaceProvider]) -> Any:
    """Return a copy of ``step`` with interpolatable string fields substituted.

    Supports ``command``, ``prompt``, ``script_path``, ``run``, and string values
    inside ``env``. Unknown attributes are left untouched.
    """
    if not providers:
        return step

    updates = _interpolated_field_updates(step, providers)
    new_env = _interpolated_env(step, providers)
    if new_env is not None:
        updates["env"] = new_env
    if not updates:
        return step
    return step.model_copy(update=updates)
