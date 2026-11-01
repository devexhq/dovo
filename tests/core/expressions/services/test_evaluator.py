"""Tests for evaluating an expression: comparison results, truthiness and the detail text."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from dovo.core.expressions import (
    ABSENT,
    Evaluation,
    InvalidExpression,
    Path,
    Resolver,
    evaluate_expression,
    is_truthy,
    parse_expression,
)


class _MappingResolver(Resolver):
    """Reads each path by its source text from a mapping; an unmapped path is ABSENT."""

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = values

    def resolve(self, path: Path) -> object:
        return self._values.get(path.text, ABSENT)


def _evaluate(source: str, values: Mapping[str, object]) -> Evaluation:
    expression = parse_expression(source)
    assert not isinstance(expression, InvalidExpression)
    return evaluate_expression(expression, _MappingResolver(values))


_COMPARED_PATHS = ("steps.a.stdout", "steps.a.outputs.x", "steps.a.outputs.ok", "steps.a.outputs.files")
"""The paths the comparison cases read; each case reads one of them, mapped to its value."""


class ExpressionEvaluatorTests:
    """evaluate_expression compares two operands, or reads a lone one by truthiness, through the caller's resolver."""

    @pytest.mark.parametrize(
        ("source", "value", "expected"),
        [
            pytest.param('steps.a.stdout == "ok"', "ok", Evaluation(True, True, "ok", "ok", "TRUE"), id="string_eq"),
            pytest.param(
                'steps.a.stdout != "ok"',
                "ok",
                Evaluation(False, True, "ok", "ok", "FALSE (was 'ok')"),
                id="string_ne",
            ),
            pytest.param("steps.a.stdout < 2", 1, Evaluation(True, True, 1, 2, "TRUE"), id="int_lt"),
            pytest.param("steps.a.stdout <= 2", 3, Evaluation(False, True, 3, 2, "FALSE (was 3)"), id="int_le"),
            pytest.param("steps.a.stdout > 2", 3, Evaluation(True, True, 3, 2, "TRUE"), id="int_gt"),
            pytest.param("steps.a.stdout >= 2", 2, Evaluation(True, True, 2, 2, "TRUE"), id="int_ge"),
            pytest.param("steps.a.stdout < 2.5", 2.25, Evaluation(True, True, 2.25, 2.5, "TRUE"), id="decimal"),
            pytest.param("3 == steps.a.stdout", 3, Evaluation(True, True, 3, 3, "TRUE"), id="number_on_the_left"),
            pytest.param(
                'steps.a.stdout > "a"',
                "b",
                Evaluation(False, True, "b", "a", "FALSE (was 'b')"),
                id="string_ordering_is_false",
            ),
            pytest.param(
                'steps.a.stdout contains "ll"',
                "hello",
                Evaluation(True, True, "hello", "ll", "TRUE"),
                id="contains_substring",
            ),
            pytest.param(
                "steps.a.outputs.x contains 2",
                [1, 2],
                Evaluation(True, True, [1, 2], 2, "TRUE"),
                id="contains_list_item",
            ),
            pytest.param(
                'steps.a.outputs.x contains "k"',
                {"k": 1},
                Evaluation(True, True, {"k": 1}, "k", "TRUE"),
                id="contains_object_key",
            ),
            pytest.param(
                "steps.a.stdout contains 4", 1042, Evaluation(True, True, 1042, 4, "TRUE"), id="contains_number_as_text"
            ),
            pytest.param(
                'steps.a.outputs.x == "x"',
                None,
                Evaluation(False, True, None, "x", "FALSE (not found)"),
                id="null_left",
            ),
            pytest.param(
                'steps.a.outputs.x != "x"',
                None,
                Evaluation(False, True, None, "x", "FALSE (not found)"),
                id="null_ne_is_false",
            ),
            pytest.param(
                "steps.a.outputs.ok == TRUE",
                True,
                Evaluation(True, True, True, True, "TRUE"),
                id="bool_literal_any_case",
            ),
            pytest.param(
                "steps.a.outputs.ok == 1", True, Evaluation(True, True, True, 1, "TRUE"), id="bool_against_one"
            ),
            pytest.param(
                'steps.a.outputs.files contains "a.py"',
                '["ba.py"]',
                Evaluation(True, True, '["ba.py"]', "a.py", "TRUE"),
                id="json_array_string_contains",
            ),
        ],
    )
    def test_comparison_gives_the_previous_result(self, source: str, value: object, expected: Evaluation) -> None:
        assert _evaluate(source, dict.fromkeys(_COMPARED_PATHS, value)) == expected

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("steps.gate.outputs.count == 3", id="equals"),
            pytest.param("steps.gate.outputs.count != 4", id="not_equals"),
            pytest.param("steps.gate.outputs.count > 2", id="greater"),
            pytest.param("steps.gate.outputs.count < 4", id="less"),
            pytest.param("steps.gate.outputs.count >= 3", id="greater_or_equal"),
            pytest.param("steps.gate.outputs.count <= 3", id="less_or_equal"),
            pytest.param("steps.gate.outputs.ratio < 3", id="decimal"),
            pytest.param("3 == steps.gate.outputs.count", id="number_on_the_left"),
        ],
    )
    def test_numeric_string_compares_numerically(self, source: str) -> None:
        values = {"steps.gate.outputs.count": "3", "steps.gate.outputs.ratio": "2.5"}

        result = _evaluate(source, values)

        assert (result.passed, result.detail) == (True, "TRUE")

    @pytest.mark.parametrize(
        "operator",
        [
            pytest.param(">", id="greater"),
            pytest.param("<", id="less"),
            pytest.param(">=", id="greater_or_equal"),
            pytest.param("<=", id="less_or_equal"),
        ],
    )
    def test_non_numeric_string_against_a_number_is_false(self, operator: str) -> None:
        result = _evaluate(f"steps.a.outputs.verdict {operator} 2", {"steps.a.outputs.verdict": "abc"})

        assert result == Evaluation(False, True, "abc", 2, "FALSE (was 'abc')")

    @pytest.mark.parametrize(
        ("value", "source"),
        [
            pytest.param("nan", "steps.gate.outputs.value == 0", id="nan"),
            pytest.param("inf", "steps.gate.outputs.value > 0", id="infinity"),
            pytest.param("1e3", "steps.gate.outputs.value == 1000", id="exponent"),
            pytest.param("1_000", "steps.gate.outputs.value == 1000", id="underscore"),
            pytest.param(" 3", "steps.gate.outputs.value == 3", id="padded"),
            pytest.param("3\n", "steps.gate.outputs.value == 3", id="trailing_newline"),
            pytest.param("٣", "steps.gate.outputs.value == 3", id="unicode_digit"),
        ],
    )
    def test_other_number_forms_stay_strings(self, value: str, source: str) -> None:
        result = _evaluate(source, {"steps.gate.outputs.value": value})

        assert (result.passed, result.actual) == (False, value)

    @pytest.mark.parametrize(
        ("source", "values", "expected"),
        [
            pytest.param(
                "steps.a.exit_code == 0",
                {},
                Evaluation(False, False, None, 0, "FALSE (not found)"),
                id="left_absent",
            ),
            pytest.param(
                "steps.a.exit_code == steps.b.exit_code",
                {"steps.a.exit_code": 0},
                Evaluation(False, False, 0, None, "FALSE (was 0)"),
                id="right_absent",
            ),
            pytest.param(
                "steps.a.exit_code == steps.b.exit_code",
                {},
                Evaluation(False, False, None, None, "FALSE (not found)"),
                id="both_absent",
            ),
        ],
    )
    def test_absent_operand_is_unresolved(
        self, source: str, values: Mapping[str, object], expected: Evaluation
    ) -> None:
        assert _evaluate(source, values) == expected

    @pytest.mark.parametrize(
        ("value", "truthy"),
        [
            pytest.param(False, False, id="false"),
            pytest.param(0, False, id="zero"),
            pytest.param(0.0, False, id="zero_float"),
            pytest.param("", False, id="empty_string"),
            pytest.param(ABSENT, False, id="absent"),
            pytest.param(None, False, id="null"),
            pytest.param(True, True, id="true"),
            pytest.param(1, True, id="one"),
            pytest.param("false", True, id="string_false"),
            pytest.param("[]", True, id="string_brackets"),
            pytest.param("0", True, id="string_zero"),
            pytest.param("x", True, id="non_empty_string"),
            pytest.param([], True, id="empty_list"),
            pytest.param({}, True, id="empty_object"),
        ],
    )
    def test_is_truthy(self, value: object, truthy: bool) -> None:
        assert is_truthy(value) is truthy

    @pytest.mark.parametrize(
        ("source", "values", "expected"),
        [
            pytest.param(
                "steps.a.stdout",
                {"steps.a.stdout": "x"},
                Evaluation(True, True, "x", None, "TRUE"),
                id="truthy_path",
            ),
            pytest.param(
                "steps.a.stdout",
                {"steps.a.stdout": ""},
                Evaluation(False, True, "", None, "FALSE (was '')"),
                id="falsy_path",
            ),
            pytest.param(
                "steps.a.stdout",
                {},
                Evaluation(False, False, None, None, "FALSE (not found)"),
                id="absent_path",
            ),
            pytest.param("0", {}, Evaluation(False, True, 0, None, "FALSE (was 0)"), id="falsy_literal"),
        ],
    )
    def test_lone_operand_passes_when_truthy(
        self, source: str, values: Mapping[str, object], expected: Evaluation
    ) -> None:
        assert _evaluate(source, values) == expected
