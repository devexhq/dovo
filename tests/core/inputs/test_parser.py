"""Unit tests for blueprint input type coercion."""

from __future__ import annotations

import re

import pytest

from dovo.core.inputs import InputType, coerce_input_value

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
    def test_coerce_boolean_accepts_valid_forms(self, token: str, expected: bool) -> None:
        assert coerce_input_value(token, InputType.BOOLEAN, name="flag") is expected

    @pytest.mark.parametrize("token", INVALID_BOOLEAN_CASES)
    def test_coerce_boolean_rejects_invalid_values(self, token: str) -> None:
        with pytest.raises(ValueError, match=rf"^Input 'flag' expects a boolean, got '{re.escape(token)}'\.$"):
            coerce_input_value(token, InputType.BOOLEAN, name="flag")
