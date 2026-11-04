"""Unit tests for plain-text input resolution error messages."""

from __future__ import annotations

from dovo.core.inputs import (
    InputType,
    ParameterInput,
    format_missing_inputs_error,
)


class InputErrorMessageTests:
    """Unit tests verifying missing-input message formatting."""

    def test_format_missing_inputs_error_includes_cli_usage_example(self) -> None:
        declarations = {
            "message": ParameterInput(
                type=InputType.STRING,
                required=True,
                aliases=["-m", "--message"],
            ),
            "target": ParameterInput(
                type=InputType.STRING,
                required=True,
            ),
        }

        error_message = format_missing_inputs_error(
            name="deploy",
            missing=["message", "target"],
            declarations=declarations,
        )
        expected_message = (
            "Missing required input 'message' for 'deploy'.\n"
            "Also missing: 'target'.\n\n"
            "Usage:\n"
            "  dovo run deploy -m <value>\n"
            "  dovo run deploy -i message=<value>\n"
            "  dovo run deploy -i target=<value>"
        )

        assert error_message == expected_message
