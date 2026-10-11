"""Inputs domain entrypoint."""

from __future__ import annotations

from typing import Any

from dovo.core.inputs.models import InputResolveResult, InputType, ParameterInput
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


class Inputs:
    """Unified entrypoint for blueprint parameter inputs and variable interpolation."""

    @staticmethod
    def resolve(
        declarations: dict[str, ParameterInput],
        *,
        cli_args: list[str] | None = None,
        overrides: dict[str, str | int | bool] | None = None,
    ) -> InputResolveResult:
        """Parse CLI args, apply overrides/defaults, and collect missing required inputs."""
        return resolve_inputs(declarations, cli_args=cli_args, overrides=overrides)

    @staticmethod
    def parse_cli_args(
        args: list[str],
        declarations: dict[str, ParameterInput],
    ) -> InputResolveResult:
        """Parse trailing CLI tokens against declared input aliases and ``-i`` overrides."""
        return parse_cli_input_args(args, declarations)

    @staticmethod
    def coerce(raw: str, input_type: InputType, *, name: str = "") -> str | int | bool:
        """Coerce a CLI string into the declared input type."""
        return coerce_input_value(raw, input_type, name=name)

    @staticmethod
    def interpolate(
        template: str,
        *,
        inputs: dict[str, Any] | None = None,
        metadata: Any | None = None,
    ) -> str:
        """Interpolate `${{ inputs.* }}` and execution variables in a string template."""
        return interpolate_string(
            template,
            inputs=inputs,
            metadata=metadata,
        )

    @staticmethod
    def interpolate_step(
        step: Any,
        *,
        inputs: dict[str, Any] | None = None,
        metadata: Any | None = None,
    ) -> Any:
        """Interpolate variables across all templateable fields of a step."""
        return interpolate_step_fields(step, inputs=inputs, metadata=metadata)

    @staticmethod
    def format_spec(name: str, spec: ParameterInput) -> str:
        """Format a single parameter specification into a readable string."""
        return format_input_spec(name, spec)

    @staticmethod
    def format_error(
        *,
        name: str,
        result: InputResolveResult,
        declarations: dict[str, ParameterInput],
        kind: str | None = None,
    ) -> str:
        """Return the first parse error, or the structured missing-input body."""
        return format_input_error_message(
            name=name,
            result=result,
            declarations=declarations,
        )

    @staticmethod
    def format_missing_error(
        *,
        name: str,
        missing: list[str],
        declarations: dict[str, ParameterInput],
        kind: str | None = None,
    ) -> str:
        """Build the structured missing-input failure message with usage hints."""
        return format_missing_inputs_error(
            name=name,
            missing=missing,
            declarations=declarations,
        )
