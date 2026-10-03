"""Unit tests for blueprint input parameter resolution and diagnostics."""

from __future__ import annotations

import re

import pytest

from dovo.core.inputs.models import (
    InputType,
    ParameterInput,
)
from dovo.core.inputs.services.resolve import (
    format_missing_inputs_error,
    is_truthy_bool,
    resolve_inputs,
)

VALID_BOOLEAN_CASES = [
    pytest.param("1", True, id="numeric_one_true"),
    pytest.param("true", True, id="lowercase_true"),
    pytest.param("True", True, id="titlecase_true"),
    pytest.param("TRUE", True, id="uppercase_true"),
    pytest.param("yes", True, id="lowercase_yes"),
    pytest.param("Yes", True, id="titlecase_yes"),
    pytest.param("YES", True, id="uppercase_yes"),
    pytest.param("y", True, id="lowercase_y"),
    pytest.param("Y", True, id="uppercase_y"),
    pytest.param("on", True, id="lowercase_on"),
    pytest.param("On", True, id="titlecase_on"),
    pytest.param("ON", True, id="uppercase_on"),
    pytest.param(" true ", True, id="whitespace_padded_true"),
    pytest.param("0", False, id="numeric_zero_false"),
    pytest.param("false", False, id="lowercase_false"),
    pytest.param("False", False, id="titlecase_false"),
    pytest.param("FALSE", False, id="uppercase_false"),
    pytest.param("no", False, id="lowercase_no"),
    pytest.param("No", False, id="titlecase_no"),
    pytest.param("NO", False, id="uppercase_no"),
    pytest.param("n", False, id="lowercase_n"),
    pytest.param("N", False, id="uppercase_n"),
    pytest.param("off", False, id="lowercase_off"),
    pytest.param("Off", False, id="titlecase_off"),
    pytest.param("OFF", False, id="uppercase_off"),
    pytest.param(" false ", False, id="whitespace_padded_false"),
]

INVALID_BOOLEAN_CASES = [
    pytest.param("maybe", id="word_maybe"),
    pytest.param("invalid", id="word_invalid"),
    pytest.param("2", id="numeric_two"),
    pytest.param("-1", id="negative_one"),
    pytest.param("none", id="word_none"),
    pytest.param("", id="empty_string"),
    pytest.param(" trueish ", id="partial_token"),
    pytest.param("null", id="word_null"),
]


class InputTypeConversionTests:
    """Unit tests verifying boolean string conversion and rejection contracts."""

    @pytest.mark.parametrize(("token", "expected"), VALID_BOOLEAN_CASES)
    def test_convert_input_truthy_bool_evaluates_valid_forms(self, token: str, expected: bool) -> None:
        """Evaluate truthy and falsy string forms into expected booleans."""
        assert is_truthy_bool(token) is expected

    @pytest.mark.parametrize("token", INVALID_BOOLEAN_CASES)
    def test_convert_input_truthy_bool_rejects_invalid_values(self, token: str) -> None:
        """Reject non-boolean tokens with ValueError naming the invalid value."""
        with pytest.raises(ValueError, match=rf"invalid boolean value '{re.escape(token)}'"):
            is_truthy_bool(token)


def _sample_declarations() -> dict[str, ParameterInput]:
    """Build standard input declarations for testing."""
    return {
        "message": ParameterInput(
            type=InputType.STRING,
            required=True,
            aliases=["-m", "--message"],
        ),
        "allow_empty": ParameterInput(
            type=InputType.BOOLEAN,
            default=False,
            aliases=["--allow-empty"],
        ),
    }


MALFORMED_SYNTAX_CASES = [
    pytest.param(["-i", "malformed_token"], id="short_input_flag_space"),
    pytest.param(["--input", "malformed_token"], id="long_input_flag_space"),
    pytest.param(["-i=malformed_token"], id="short_input_flag_equals"),
    pytest.param(["--input=malformed_token"], id="long_input_flag_equals"),
]


class InputSyntaxValidationTests:
    """Unit tests verifying CLI input syntax validation and diagnostic formatting."""

    @pytest.mark.parametrize("cli_args", MALFORMED_SYNTAX_CASES)
    def test_resolve_inputs_invalid_syntax_names_malformed_token(self, cli_args: list[str]) -> None:
        """Reject malformed input overrides and report expected syntax error."""
        result = resolve_inputs(_sample_declarations(), cli_args=cli_args)

        assert result.values == {}
        assert result.missing == []
        assert result.errors == ["Invalid input syntax 'malformed_token'. Expected key=value (e.g. -i message=value)."]
        assert result.warnings == []
        assert result.fixes == []

    def test_resolve_inputs_warns_on_unrecognized_cli_options(self) -> None:
        """Collect warnings on unrecognized options and unexpected positional arguments."""
        result = resolve_inputs(
            _sample_declarations(),
            cli_args=["-m", "ship it", "--unrecognized-option", "positional_arg"],
        )

        assert result.values == {"message": "ship it", "allow_empty": False}
        assert result.missing == []
        assert result.errors == []
        assert result.warnings == [
            "Ignoring unrecognized option '--unrecognized-option'.",
            "Ignoring unexpected argument 'positional_arg'.",
        ]
        assert result.fixes == []

    def test_format_missing_inputs_error_includes_cli_usage_example(self) -> None:
        """Format structured missing-input error with actionable CLI usage examples."""
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


FLAG_RESOLUTION_CASES = [
    pytest.param(
        ["-m", "ship it"],
        {"message": "ship it", "allow_empty": False},
        id="short_flag_with_space",
    ),
    pytest.param(
        ["-m=ship it"],
        {"message": "ship it", "allow_empty": False},
        id="short_flag_with_equals",
    ),
    pytest.param(
        ["--message", "ship it"],
        {"message": "ship it", "allow_empty": False},
        id="long_flag_with_space",
    ),
    pytest.param(
        ["--message=ship it"],
        {"message": "ship it", "allow_empty": False},
        id="long_flag_with_equals",
    ),
    pytest.param(
        ["-m", "ship it", "--allow-empty"],
        {"message": "ship it", "allow_empty": True},
        id="bare_boolean_flag_resolves_true",
    ),
    pytest.param(
        ["-m", "ship it", "--allow-empty=false"],
        {"message": "ship it", "allow_empty": False},
        id="boolean_flag_with_explicit_false",
    ),
    pytest.param(
        ["-i", "message=override_val", "--allow-empty"],
        {"message": "override_val", "allow_empty": True},
        id="generic_override_short_flag",
    ),
    pytest.param(
        ["--input", "message=override_val", "--allow-empty"],
        {"message": "override_val", "allow_empty": True},
        id="generic_override_long_flag",
    ),
]


class InputFlagResolutionTests:
    """Unit tests verifying resolution of declared CLI flag aliases and default values."""

    @pytest.mark.parametrize(("cli_args", "expected_values"), FLAG_RESOLUTION_CASES)
    def test_resolve_supports_declared_cli_flag_aliases(
        self,
        cli_args: list[str],
        expected_values: dict[str, str | int | bool],
    ) -> None:
        """Resolve declared flag aliases and generic overrides into typed parameter values."""
        result = resolve_inputs(_sample_declarations(), cli_args=cli_args)

        assert result.values == expected_values
        assert result.missing == []
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

    def test_resolve_prioritizes_declared_alias_matching_generic_flag(self) -> None:
        """Resolve declared '-i' flag alias onto target input instead of treating as generic override."""
        inputs = {
            "issue": ParameterInput(
                type=InputType.STRING,
                required=True,
                aliases=["-i", "--issue"],
            ),
        }
        result = resolve_inputs(inputs, cli_args=["-i", "616"])

        assert result.values == {"issue": "616"}
        assert result.missing == []
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
