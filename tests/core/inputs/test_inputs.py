"""Unit tests for blueprint input resolution and the package's public surface."""

from __future__ import annotations

import pytest

import dovo.core.inputs
from dovo.core.inputs import InputType, ParameterInput, resolve_inputs


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
        result = resolve_inputs(_sample_declarations(), cli_args=cli_args)

        assert result.values == {}
        assert result.missing == []
        assert result.errors == ["Invalid input syntax 'malformed_token'. Expected key=value (e.g. -i message=value)."]
        assert result.warnings == []
        assert result.fixes == []

    def test_resolve_inputs_warns_on_unrecognized_cli_options(self) -> None:
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
        result = resolve_inputs(_sample_declarations(), cli_args=cli_args)

        assert result.values == expected_values
        assert result.missing == []
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

    def test_resolve_prioritizes_declared_alias_matching_generic_flag(self) -> None:
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


def _declarations_with_count() -> dict[str, ParameterInput]:
    """Build the standard declarations plus an optional integer input."""
    return {**_sample_declarations(), "count": ParameterInput(type=InputType.INTEGER)}


PARSE_FAILURE_CASES = [
    pytest.param(["-m"], ["Missing value for option '-m'."], [], id="alias_missing_value"),
    pytest.param(["-i"], ["Missing value for option '-i'."], [], id="generic_missing_value"),
    pytest.param(["-i", "count=abc"], ["Input 'count' expects an integer, got 'abc'."], [], id="invalid_integer"),
    pytest.param(
        ["--allow-empty=maybe"], ["Input 'allow_empty' expects a boolean, got 'maybe'."], [], id="invalid_boolean"
    ),
]


class InputResolutionOutcomeTests:
    """Unit tests verifying resolution outcomes: parse failures, overrides, defaults and missing inputs."""

    @pytest.mark.parametrize(("cli_args", "errors", "warnings"), PARSE_FAILURE_CASES)
    def test_resolve_inputs_reports_parse_failures_verbatim(
        self, cli_args: list[str], errors: list[str], warnings: list[str]
    ) -> None:
        result = resolve_inputs(_declarations_with_count(), cli_args=cli_args)

        assert result.values == {}
        assert result.missing == []
        assert result.errors == errors
        assert result.warnings == warnings

    def test_resolve_inputs_warns_on_unknown_override_key(self) -> None:
        result = resolve_inputs(_sample_declarations(), cli_args=["-m", "x", "-i", "nope=1"])

        assert result.values == {"message": "x", "allow_empty": False}
        assert result.missing == []
        assert result.errors == []
        assert result.warnings == ["Ignoring unknown input override 'nope'."]

    def test_resolve_inputs_overrides_beat_cli_values_and_defaults_fill_gaps(self) -> None:
        result = resolve_inputs(
            _sample_declarations(),
            cli_args=["-m", "cli"],
            overrides={"message": "from-override"},
        )

        assert result.values == {"message": "from-override", "allow_empty": False}
        assert result.missing == []
        assert result.errors == []
        assert result.warnings == []

    def test_resolve_inputs_lists_missing_required_inputs(self) -> None:
        result = resolve_inputs(_sample_declarations())

        assert result.missing == ["message"]
        assert result.values == {"allow_empty": False}
        assert result.ok is False


class InputsPackageTests:
    """Unit tests pinning the public names exported by dovo.core.inputs."""

    def test_package_exports_named_functions_only(self) -> None:
        assert sorted(dovo.core.inputs.__all__) == [
            "ExecutionMetadataView",
            "InputResolveResult",
            "InputType",
            "NamespaceProvider",
            "NamespaceResolutionError",
            "ParameterInput",
            "build_providers",
            "coerce_input_value",
            "format_input_error_message",
            "format_missing_inputs_error",
            "interpolate_step_fields",
            "interpolate_string",
            "parse_cli_input_args",
            "resolve_inputs",
        ]
        assert not hasattr(dovo.core.inputs, "Inputs")
