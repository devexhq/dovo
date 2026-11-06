"""Remediations for blueprint inputs a run left out."""

from __future__ import annotations

from dovo.common.models import Remediation
from dovo.core.inputs.models import ParameterInput


def missing_inputs_remediation(
    *,
    name: str,
    missing: list[str],
    declarations: dict[str, ParameterInput],
) -> Remediation:
    """Return the step that runs the blueprint called name again with a value for every missing input.

    Each input is passed by its first option alias when it has one, else as `-i <input>=<value>`, or as
    `--input <input>=<value>` when a declared input takes `-i` as its own alias.
    """
    generic = "--input" if any("-i" in spec.aliases for spec in declarations.values()) else "-i"
    flags = " ".join(_input_flag(item, declarations[item], generic) for item in missing)
    command = f"dovo run {name} {flags}"
    return Remediation(message=f"Run '{command}'", command=command)


def _input_flag(name: str, spec: ParameterInput, generic: str) -> str:
    """Return the flag that passes input name, with a kebab-case placeholder for its value."""
    placeholder = f"<{name.replace('_', '-')}>"
    alias = next((item for item in spec.aliases if item.startswith("-")), None)
    if alias is not None:
        return f"{alias} {placeholder}"
    return f"{generic} {name}={placeholder}"
