"""Plain-text formatters for blueprint input resolution errors."""

from __future__ import annotations

from dovo.core.inputs.models import InputResolveResult, ParameterInput


def format_missing_inputs_error(
    *,
    name: str,
    missing: list[str],
    declarations: dict[str, ParameterInput],
) -> str:
    """Build the structured missing-input failure message with usage hints."""
    primary = missing[0]
    lines = [f"Missing required input '{primary}' for '{name}'."]
    if len(missing) > 1:
        extras = ", ".join(f"'{item}'" for item in missing[1:])
        lines.append(f"Also missing: {extras}.")
    lines.append("")
    lines.append("Usage:")
    for input_name in missing:
        spec = declarations[input_name]
        alias = next((a for a in spec.aliases if a.startswith("-")), None)
        if alias is not None:
            lines.append(f"  dovo run {name} {alias} <value>")
        lines.append(f"  dovo run {name} -i {input_name}=<value>")
    return "\n".join(lines)


def format_input_error_message(
    *,
    name: str,
    result: InputResolveResult,
    declarations: dict[str, ParameterInput],
) -> str:
    """Return the first parse error, or the structured missing-input body."""
    if result.errors:
        return result.errors[0]
    return format_missing_inputs_error(
        name=name,
        missing=result.missing,
        declarations=declarations,
    )
