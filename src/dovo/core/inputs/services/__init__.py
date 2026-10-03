"""Input domain services: CLI resolution, interpolation, and rendering."""

from dovo.core.inputs.services.interpolate import (
    interpolate_step_fields,
    interpolate_string,
)
from dovo.core.inputs.services.renderer import format_input_spec
from dovo.core.inputs.services.resolve import (
    coerce_input_value,
    format_input_error_message,
    format_missing_inputs_error,
    parse_cli_input_args,
    resolve_inputs,
)

__all__ = [
    "coerce_input_value",
    "format_input_error_message",
    "format_input_spec",
    "format_missing_inputs_error",
    "interpolate_step_fields",
    "interpolate_string",
    "parse_cli_input_args",
    "resolve_inputs",
]
