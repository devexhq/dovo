"""Template variable interpolation for resolved blueprint inputs and execution metadata.

Each `{{ ... }}` or `${{ ... }}` placeholder runs to the first `}}` outside a quoted string, and its body is parsed by
the expression grammar. A body that is a lone path is replaced by the value of the first namespace provider that
defines the path; a lone path no provider defines, a lone literal and a body that is not an expression stay as
written. Any other expression is replaced by its value, a path no provider defines reading as absent inside it. Values
render as recorded outputs hold them, with an absent value as the empty string.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel

from dovo.core.expressions import ABSENT, InvalidExpression, Literal, Path, evaluate_value, parse_expression
from dovo.core.inputs.models import UNDEFINED
from dovo.core.inputs.step_outputs import render_step_output, resolve_step_output

_QUOTES = ("'", '"')
_INTERPOLATED_FIELDS = ("command", "prompt", "script_path", "run")
_STEP_ENTRY_FIELDS = {"id", "name", "index", "status", "exit_code"}


def _extract_iteration_index(meta: object) -> str:
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
    "previous_step.id": lambda meta: str(meta.previous_step.id),
    "previous_step.name": lambda meta: str(meta.previous_step.name),
    "previous_step.index": lambda meta: str(meta.previous_step.index),
    "previous_step.status": lambda meta: str(meta.previous_step.status),
    "previous_step.exit_code": lambda meta: str(meta.previous_step.exit_code),
    "iteration.index": _extract_iteration_index,
    "iteration.attempt": _extract_iteration_index,
}


def _find_step_by_id(steps: Sequence[object], step_id: str) -> object:
    """Find the most recent step matching step_id in steps list."""
    for entry in reversed(steps):
        if getattr(entry, "id", None) == step_id:
            return entry
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
    """Resolves placeholder paths for one namespace."""

    namespace: str

    def resolve(self, path: Path) -> object:
        """Return the value path names, ABSENT when this namespace defines path but holds no value, or UNDEFINED when it does not define path."""
        ...


class InputsProvider:
    """Resolves `inputs.<name>` and bare `<name>` references against the resolved blueprint inputs."""

    namespace = "inputs"

    def __init__(self, inputs: Mapping[str, object]) -> None:
        self._inputs = inputs

    def resolve(self, path: Path) -> object:
        """Return the value of `inputs.<name>` or a bare `<name>` present in inputs, else UNDEFINED."""
        if path.root == "inputs" and len(path.segments) == 1:
            name = path.segments[0]
        elif not path.segments:
            name = path.root
        else:
            return UNDEFINED
        if isinstance(name, str) and name in self._inputs:
            return self._inputs[name]
        return UNDEFINED


class StepsProvider:
    """Resolves `steps[<selector>].<field>` and `steps[<selector>].outputs.<key>` against execution metadata."""

    namespace = "steps"

    def __init__(self, metadata: ExecutionMetadataView) -> None:
        self._steps: list[object] = list(metadata.steps or [])

    def resolve(self, path: Path) -> object:
        """Return the step field or output value (recorded, else the stdout JSON key, following any further path).

        ABSENT for an unknown step, an out-of-range index or a missing output; UNDEFINED for a path that is not a
        step field or an output key.
        """
        if path.root != "steps" or len(path.segments) < 2:
            return UNDEFINED
        selector, field_name, *rest = path.segments
        entry = self._select(selector)
        if field_name == "outputs" and rest:
            return _step_output(entry, rest[0], rest[1:])
        if field_name in _STEP_ENTRY_FIELDS and not rest:
            return ABSENT if entry is None else getattr(entry, field_name, ABSENT)
        return UNDEFINED

    def _select(self, selector: str | int) -> object:
        """Return the step at an int position within range or the most recent step with a str id, else None."""
        if isinstance(selector, int):
            if -len(self._steps) <= selector < len(self._steps):
                return self._steps[selector]
            return None
        return _find_step_by_id(self._steps, selector)


def _step_output(entry: object, key: str | int, rest: Sequence[str | int]) -> object:
    """Return the output key of entry, following rest through its parsed JSON; ABSENT when there is none."""
    if entry is None or isinstance(key, int):
        return ABSENT
    outputs = getattr(entry, "outputs", None) or {}
    stdout = getattr(entry, "stdout", None) or ""
    found = resolve_step_output(outputs, stdout, key, rest)
    return ABSENT if found is None else found.value


class MetadataProvider:
    """Resolves one execution-metadata namespace such as `step`, `blueprint` or `previous_step`."""

    def __init__(self, namespace: str, metadata: ExecutionMetadataView) -> None:
        self.namespace = namespace
        self._metadata = metadata

    def resolve(self, path: Path) -> object:
        """Return the extractor value for a `<namespace>.<field>` path, or UNDEFINED when it is not defined."""
        if path.root != self.namespace or not all(isinstance(segment, str) for segment in path.segments):
            return UNDEFINED

        extractor = _METADATA_EXTRACTORS.get(".".join((path.root, *map(str, path.segments))))
        if extractor is None:
            return UNDEFINED

        try:
            return extractor(self._metadata)
        except AttributeError:
            return UNDEFINED


def _metadata_providers(metadata: ExecutionMetadataView) -> list[NamespaceProvider]:
    """Build one MetadataProvider per metadata namespace plus the StepsProvider."""
    namespaces = dict.fromkeys(key.partition(".")[0] for key in _METADATA_EXTRACTORS)
    providers: list[NamespaceProvider] = [MetadataProvider(namespace, metadata) for namespace in namespaces]
    providers.append(StepsProvider(metadata))
    return providers


def build_providers(
    *,
    inputs: Mapping[str, object] | None = None,
    metadata: ExecutionMetadataView | None = None,
    artifacts: NamespaceProvider | None = None,
) -> list[NamespaceProvider]:
    """Return providers in resolution order: metadata, steps, artifacts, then inputs; omit those not supplied."""
    providers: list[NamespaceProvider] = []
    if metadata is not None:
        providers.extend(_metadata_providers(metadata))
    if artifacts is not None:
        providers.append(artifacts)
    if inputs:
        providers.append(InputsProvider(inputs))
    return providers


def interpolate_string(template: str, providers: Sequence[NamespaceProvider]) -> str:
    """Replace ``${{ ... }}`` and ``{{ ... }}`` placeholders with what their bodies render, leaving the rest as written."""
    if not providers:
        return template
    parts: list[str] = []
    position = 0
    for start, end, body in _placeholders(template):
        parts.append(template[position:start])
        parts.append(_replacement(template[start:end], body, providers))
        position = end
    parts.append(template[position:])
    return "".join(parts)


def _replacement(placeholder: str, body: str, providers: Sequence[NamespaceProvider]) -> str:
    """Return what placeholder renders: a lone defined path's value, any compound expression's value, else itself."""
    expression = parse_expression(body.strip())
    if isinstance(expression, (InvalidExpression, Literal)):
        return placeholder
    if isinstance(expression, Path):
        value = _lookup(expression, providers)
        return placeholder if value is UNDEFINED else _render(value)
    return _render(evaluate_value(expression, _PlaceholderResolver(providers)))


class _PlaceholderResolver:
    """Resolver reading each path of a compound placeholder body from the providers, an undefined path as ABSENT."""

    def __init__(self, providers: Sequence[NamespaceProvider]) -> None:
        self._providers = providers

    def resolve(self, path: Path) -> object:
        """Return the first provider's value for path, or ABSENT when no provider defines it."""
        value = _lookup(path, self._providers)
        return ABSENT if value is UNDEFINED else value


def _placeholders(template: str) -> Iterator[tuple[int, int, str]]:
    """Yield the start, end and body of each placeholder: from `{{`, with a `$` straight before it, past its `}}`."""
    position = template.find("{{")
    while position != -1:
        close = _placeholder_close(template, position + 2)
        if close is None:
            return
        start = position - 1 if position and template[position - 1] == "$" else position
        yield start, close + 2, template[position + 2 : close]
        position = template.find("{{", close + 2)


def _placeholder_close(template: str, position: int) -> int | None:
    """Return the index of the first `}}` from position outside a quoted string, else of the first `}}`, else None.

    A backslash-escaped quote does not close a string; a string never closed falls back to the first `}}`.
    """
    first = template.find("}}", position)
    if first == -1:
        return None
    while position < len(template):
        if template.startswith("}}", position):
            return position
        if template[position] not in _QUOTES:
            position += 1
            continue
        after = _after_quoted(template, position)
        if after is None:
            return first
        position = after
    return first


def _after_quoted(template: str, start: int) -> int | None:
    """Return the index after the closing quote of the string opened at start, or None when it never closes."""
    quote = template[start]
    position = start + 1
    while position < len(template):
        char = template[position]
        if char == quote:
            return position + 1
        position += 2 if char == "\\" else 1
    return None


def _lookup(path: Path, providers: Sequence[NamespaceProvider]) -> object:
    """Return the first provider result for path that is not UNDEFINED, or UNDEFINED when none defines it."""
    for provider in providers:
        value = provider.resolve(path)
        if value is not UNDEFINED:
            return value
    return UNDEFINED


def _render(value: object) -> str:
    """Render value as placeholder text: the empty string for ABSENT, else as recorded outputs hold it."""
    return "" if value is ABSENT else render_step_output(value)


def _interpolated_field_updates(step: BaseModel, providers: Sequence[NamespaceProvider]) -> dict[str, object]:
    """Compute string field replacements for an executable step model."""
    updates: dict[str, object] = {}
    for field_name in _INTERPOLATED_FIELDS:
        value = getattr(step, field_name, None)
        if isinstance(value, str) and value:
            interpolated = interpolate_string(value, providers)
            if interpolated != value:
                updates[field_name] = interpolated
    return updates


def _interpolated_env(step: BaseModel, providers: Sequence[NamespaceProvider]) -> dict[str, str] | None:
    """Compute environment dictionary replacements for an executable step model."""
    env = getattr(step, "env", None)
    if not isinstance(env, dict) or not env:
        return None
    new_env = {
        key: interpolate_string(value, providers) if isinstance(value, str) else value for key, value in env.items()
    }
    return new_env if new_env != env else None


def interpolate_step_fields[S: BaseModel](step: S, providers: Sequence[NamespaceProvider]) -> S:
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
