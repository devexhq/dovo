"""Top-level blueprint input resolution: CLI parsing, caller overrides, defaults and missing-required detection."""

from __future__ import annotations

from dovo.common.error_codes import ErrorCode
from dovo.core.inputs.models import InputResolveResult, ParameterInput
from dovo.core.inputs.parser import parse_cli_input_args
from dovo.core.inputs.renderer import format_missing_inputs_error


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
    name: str,
    cli_args: list[str] | None = None,
    overrides: dict[str, str | int | bool] | None = None,
) -> InputResolveResult:
    """Parse CLI args, apply overrides/defaults, and collect missing required inputs.

    A syntax or type error, or a missing required input, is reported as BLUEPRINT_INPUT_INVALID; a missing input's
    error is the usage message for the blueprint called name.
    """
    parsed = parse_cli_input_args(cli_args or [], declarations)
    if not parsed.ok:
        return parsed

    values = _apply_defaults_and_overrides(declarations, parsed.values, overrides)
    missing = _missing_required(declarations, values)
    if not missing:
        return InputResolveResult(values=values, warnings=list(parsed.warnings))
    return InputResolveResult(
        values=values,
        missing=missing,
        errors=[format_missing_inputs_error(name=name, missing=missing, declarations=declarations)],
        error_code=ErrorCode.BLUEPRINT_INPUT_INVALID,
        warnings=list(parsed.warnings),
    )
