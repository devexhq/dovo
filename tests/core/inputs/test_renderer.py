"""Unit tests for plain-text input resolution error messages."""

from __future__ import annotations

import pytest

from dovo.core.inputs import (
    InputResolveResult,
    InputType,
    ParameterInput,
    format_input_error_message,
    format_missing_inputs_error,
)


class InputErrorMessageTests:
    """Unit tests verifying missing-input and first-error message formatting."""

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

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            pytest.param(
                InputResolveResult(errors=["first", "second"], missing=["message"]), "first", id="first_error_wins"
            ),
            pytest.param(
                InputResolveResult(missing=["message"]),
                "Missing required input 'message' for 'deploy'.\n\nUsage:\n"
                "  dovo run deploy -m <value>\n  dovo run deploy -i message=<value>",
                id="missing_body",
            ),
        ],
    )
    def test_format_input_error_message_returns_first_error_or_missing_body(
        self, result: InputResolveResult, expected: str
    ) -> None:
        declarations = {
            "message": ParameterInput(type=InputType.STRING, required=True, aliases=["-m", "--message"]),
        }

        message = format_input_error_message(name="deploy", result=result, declarations=declarations)

        assert message == expected
