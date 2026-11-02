"""Blueprint input declarations, resolution, interpolation and formatting."""

from dovo.core.inputs.exceptions import NamespaceResolutionError
from dovo.core.inputs.inputs import resolve_inputs
from dovo.core.inputs.interpolate import (
    ExecutionMetadataView,
    NamespaceProvider,
    build_providers,
    interpolate_step_fields,
    interpolate_string,
)
from dovo.core.inputs.models import (
    UNDEFINED,
    InputResolveResult,
    InputType,
    ParameterInput,
    StepOutputValue,
    Undefined,
)
from dovo.core.inputs.parser import coerce_input_value, parse_cli_input_args
from dovo.core.inputs.renderer import format_input_error_message, format_missing_inputs_error
from dovo.core.inputs.step_outputs import render_step_output, resolve_step_output

__all__ = [
    "UNDEFINED",
    "ExecutionMetadataView",
    "InputResolveResult",
    "InputType",
    "NamespaceProvider",
    "NamespaceResolutionError",
    "ParameterInput",
    "StepOutputValue",
    "Undefined",
    "build_providers",
    "coerce_input_value",
    "format_input_error_message",
    "format_missing_inputs_error",
    "interpolate_step_fields",
    "interpolate_string",
    "parse_cli_input_args",
    "render_step_output",
    "resolve_inputs",
    "resolve_step_output",
]
