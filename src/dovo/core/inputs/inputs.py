"""Top-level blueprint input resolution: CLI parsing, caller overrides, defaults and missing-required detection."""

from __future__ import annotations

from dovo.core.inputs.models import InputResolveResult, ParameterInput
from dovo.core.inputs.parser import parse_cli_input_args


def _apply_defaults_and_overrides(
    declarations: dict[str, ParameterInput],
    values: dict[str, str | int | bool],
    overrides: dict[str, str | int | bool] | None,
) -> dict[str, str | int | bool]:
    """Merge parsed values with caller overrides and declared default values."""
    resolved = dict(values)
    if overrides:
        resolved.update(overrides)
    for name, spec in declarations.items():
        if name not in resolved and spec.default is not None:
            resolved[name] = spec.default
    return resolved


def _missing_required(
    declarations: dict[str, ParameterInput],
    values: dict[str, str | int | bool],
) -> list[str]:
    """Return names of required parameter inputs that have no assigned value."""
    return [
        name for name, spec in declarations.items() if spec.required and (name not in values or values[name] is None)
    ]


def resolve_inputs(
    declarations: dict[str, ParameterInput],
    *,
    cli_args: list[str] | None = None,
    overrides: dict[str, str | int | bool] | None = None,
) -> InputResolveResult:
    """Parse CLI args, apply overrides/defaults, and collect missing required inputs."""
    parsed = parse_cli_input_args(cli_args or [], declarations)
    if parsed.errors:
        return parsed

    values = _apply_defaults_and_overrides(declarations, parsed.values, overrides)
    return InputResolveResult(
        values=values,
        missing=_missing_required(declarations, values),
        errors=list(parsed.errors),
        warnings=list(parsed.warnings),
    )
