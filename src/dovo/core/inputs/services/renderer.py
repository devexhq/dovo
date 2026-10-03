"""Plain-text formatters for blueprint input declarations."""

from __future__ import annotations

from dovo.common.utils import enum_value
from dovo.core.inputs.models import ParameterInput


def format_input_spec(name: str, spec: ParameterInput) -> str:
    """Format a single parameter specification into a readable string."""
    required = "required" if spec.required else "optional"
    default = f", default={spec.default!r}" if spec.default is not None else ""
    aliases = f", aliases={spec.aliases}" if spec.aliases else ""
    description = f" — {spec.description}" if spec.description else ""
    return f"  - {name} ({enum_value(spec.type)}, {required}{default}{aliases}){description}"
