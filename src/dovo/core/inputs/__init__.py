"""Blueprint input declarations, resolution, interpolation and formatting."""

from dovo.core.inputs.artifact_reference import ARTIFACT_FIELDS, artifact_reference
from dovo.core.inputs.inputs import resolve_inputs
from dovo.core.inputs.interpolate import (
    ExecutionMetadataView,
    NamespaceProvider,
    build_providers,
    find_placeholders,
    interpolate_step_fields,
    interpolate_string,
    placeholder_texts,
)
from dovo.core.inputs.models import (
    UNDEFINED,
    ArtifactReference,
    InputResolveResult,
    InputType,
    ParameterInput,
    StepOutputValue,
    Undefined,
)
from dovo.core.inputs.parser import coerce_input_value, parse_cli_input_args
from dovo.core.inputs.renderer import missing_inputs_remediation
from dovo.core.inputs.step_outputs import read_json_path, render_step_output, resolve_step_output

__all__ = [
    "ARTIFACT_FIELDS",
    "UNDEFINED",
    "ArtifactReference",
    "ExecutionMetadataView",
    "InputResolveResult",
    "InputType",
    "NamespaceProvider",
    "ParameterInput",
    "StepOutputValue",
    "Undefined",
    "artifact_reference",
    "build_providers",
    "coerce_input_value",
    "find_placeholders",
    "interpolate_step_fields",
    "interpolate_string",
    "missing_inputs_remediation",
    "parse_cli_input_args",
    "placeholder_texts",
    "read_json_path",
    "render_step_output",
    "resolve_inputs",
    "resolve_step_output",
]
