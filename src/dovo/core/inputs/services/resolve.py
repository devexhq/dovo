"""CLI parsing and pre-execution resolution for blueprint inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dovo.core.inputs.models import InputResolveResult, InputType, ParameterInput

_INPUT_OVERRIDE_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$", re.DOTALL)


@dataclass
class _ParseState:
    """Mutable accumulator while walking CLI tokens."""

    values: dict[str, str | int | bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    index: int = 0


def is_truthy_bool(raw: str) -> bool:
    """Determine if the provided string is a truthy value.

    Raises ``ValueError`` if no match is found.
    """
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean value '{raw}'")


def coerce_input_value(raw: str, input_type: InputType, *, name: str) -> str | int | bool:
    """Coerce a CLI string into the declared input type."""
    if input_type == InputType.STRING:
        return raw
    if input_type == InputType.INTEGER:
        try:
            return int(raw, 10)
        except ValueError as exc:
            raise ValueError(f"Input '{name}' expects an integer, got '{raw}'.") from exc
    try:
        return is_truthy_bool(raw)
    except ValueError as exc:
        raise ValueError(f"Input '{name}' expects a boolean, got '{raw}'.") from exc


def _alias_map(declarations: dict[str, ParameterInput]) -> dict[str, str]:
    """Map CLI alias tokens onto canonical input names."""
    mapping: dict[str, str] = {}
    for name, spec in declarations.items():
        for alias in spec.aliases:
            mapping[alias] = name
    return mapping


def _take_value(args: list[str], index: int, *, flag: str) -> tuple[str, int]:
    """Read ``--flag=value`` or the next argv token as the flag value."""
    token = args[index]
    if "=" in token:
        return token.split("=", 1)[1], index + 1
    next_index = index + 1
    if next_index >= len(args):
        raise ValueError(f"Missing value for option '{flag}'.")
    return args[next_index], next_index + 1


def _is_generic_input_token(token: str) -> bool:
    """Check whether a token represents a generic -i or --input option."""
    return token in {"-i", "--input"} or token.startswith("-i=") or token.startswith("--input=")


def _match_alias_token(token: str, aliases: dict[str, str]) -> str | None:
    """Find the matching alias prefix for a given CLI token if present."""
    if token in aliases:
        return token
    for alias in aliases:
        if token.startswith(f"{alias}="):
            return alias
    return None


def _store_coerced(
    state: _ParseState,
    *,
    name: str,
    raw: str,
    input_type: InputType,
) -> None:
    """Coerce raw string value according to input type and store in state."""
    try:
        state.values[name] = coerce_input_value(raw, input_type, name=name)
    except ValueError as exc:
        state.errors.append(str(exc))


def _parse_generic_override(
    args: list[str],
    state: _ParseState,
    declarations: dict[str, ParameterInput],
) -> bool:
    """Parse one ``-i``/``--input`` token. Returns False when parsing must stop."""
    token = args[state.index]
    try:
        raw, state.index = _take_value(args, state.index, flag=token.split("=", 1)[0])
    except ValueError as exc:
        state.errors.append(str(exc))
        return False

    match = _INPUT_OVERRIDE_RE.match(raw)
    if match is None:
        state.errors.append(f"Invalid input syntax '{raw}'. Expected key=value (e.g. -i message=value).")
        return True

    name = match.group("key")
    if name not in declarations:
        state.warnings.append(f"Ignoring unknown input override '{name}'.")
        return True

    _store_coerced(
        state,
        name=name,
        raw=match.group("value"),
        input_type=declarations[name].type,
    )
    return True


def _is_bare_boolean_flag(args: list[str], index: int, token: str, spec: ParameterInput) -> bool:
    """Determine whether a token is a bare boolean flag without an attached value."""
    if spec.type != InputType.BOOLEAN or "=" in token:
        return False
    return index + 1 >= len(args) or args[index + 1].startswith("-")


def _parse_alias_token(
    args: list[str],
    state: _ParseState,
    declarations: dict[str, ParameterInput],
    aliases: dict[str, str],
    flag: str,
) -> bool:
    """Parse one declared alias token. Returns False when parsing must stop."""
    name = aliases[flag]
    spec = declarations[name]
    token = args[state.index]
    if _is_bare_boolean_flag(args, state.index, token, spec):
        state.values[name] = True
        state.index += 1
        return True

    try:
        raw, state.index = _take_value(args, state.index, flag=flag)
    except ValueError as exc:
        state.errors.append(str(exc))
        return False

    before_errors = len(state.errors)
    _store_coerced(state, name=name, raw=raw, input_type=spec.type)
    return len(state.errors) == before_errors


def _warn_unknown_token(state: _ParseState, token: str) -> None:
    """Record a warning for an unrecognized CLI argument or option and advance index."""
    if token.startswith("-"):
        state.warnings.append(f"Ignoring unrecognized option '{token}'.")
    else:
        state.warnings.append(f"Ignoring unexpected argument '{token}'.")
    state.index += 1


def _consume_one_token(
    args: list[str],
    state: _ParseState,
    declarations: dict[str, ParameterInput],
    aliases: dict[str, str],
) -> bool:
    """Consume one CLI token. Returns False when parsing must stop."""
    token = args[state.index]
    flag = _match_alias_token(token, aliases)
    if flag is not None:
        return _parse_alias_token(args, state, declarations, aliases, flag)

    if _is_generic_input_token(token):
        return _parse_generic_override(args, state, declarations)

    _warn_unknown_token(state, token)
    return True


def parse_cli_input_args(
    args: list[str],
    declarations: dict[str, ParameterInput],
) -> InputResolveResult:
    """Parse trailing CLI tokens against declared input aliases and ``-i`` overrides."""
    state = _ParseState()
    aliases = _alias_map(declarations)
    while state.index < len(args) and _consume_one_token(args, state, declarations, aliases):
        pass
    return InputResolveResult(values=state.values, errors=state.errors, warnings=state.warnings)


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
