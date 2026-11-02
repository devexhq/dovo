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
    evaluate_value,
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


class _RecordingResolver(_MappingResolver):
    """A mapping resolver that records the text of each path it resolves."""

    def __init__(self, values: Mapping[str, object]) -> None:
        super().__init__(values)
        self.read: list[str] = []

    def resolve(self, path: Path) -> object:
        self.read.append(path.text)
        return super().resolve(path)


_X_OR_Y = 'steps.a.stdout == "x" || steps.b.stdout == "y"'
_X_AND_Y = 'steps.a.stdout == "x" && steps.b.stdout == "y"'

_A = "steps.a.stdout"
_A_OR_NONE = "steps.a.stdout || 'none'"

_S = "steps.a.outputs.s"
_XY = '["x", "y"]'

_COMPARED_PATHS = ("steps.a.stdout", "steps.a.outputs.x", "steps.a.outputs.ok", "steps.a.outputs.files")
"""The paths the comparison cases read; each case reads one of them, mapped to its value."""


class ExpressionEvaluatorTests:
    """evaluate_expression passes on its value's truthiness, reporting the comparison that decided it."""

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

    @pytest.mark.parametrize(
        ("source", "values", "expected"),
        [
            pytest.param(
                _X_OR_Y,
                {"steps.a.stdout": "x", "steps.b.stdout": "n"},
                Evaluation(True, True, "x", "x", "TRUE"),
                id="or_left_decides",
            ),
            pytest.param(
                _X_OR_Y,
                {"steps.a.stdout": "n", "steps.b.stdout": "y"},
                Evaluation(True, True, "y", "y", "TRUE"),
                id="or_right_decides",
            ),
            pytest.param(
                _X_OR_Y,
                {"steps.a.stdout": "n", "steps.b.stdout": "m"},
                Evaluation(False, True, "m", "y", "FALSE (was 'm')"),
                id="or_both_false",
            ),
            pytest.param(
                _X_OR_Y,
                {"steps.b.stdout": "y"},
                Evaluation(True, True, "y", "y", "TRUE"),
                id="or_left_absent",
            ),
            pytest.param(
                _X_AND_Y,
                {"steps.a.stdout": "n", "steps.b.stdout": "y"},
                Evaluation(False, True, "n", "x", "FALSE (was 'n')"),
                id="and_left_false",
            ),
            pytest.param(
                _X_AND_Y,
                {"steps.b.stdout": "y"},
                Evaluation(False, False, None, "x", "FALSE (not found)"),
                id="and_left_absent",
            ),
            pytest.param(
                _X_AND_Y,
                {"steps.a.stdout": "x", "steps.b.stdout": "y"},
                Evaluation(True, True, "y", "y", "TRUE"),
                id="and_both_true",
            ),
            pytest.param(
                '!(steps.a.stdout == "completed")',
                {},
                Evaluation(True, False, None, "completed", "TRUE"),
                id="not_of_an_absent_comparison",
            ),
            pytest.param(
                '!(steps.a.stdout == "completed")',
                {"steps.a.stdout": "completed"},
                Evaluation(False, True, "completed", "completed", "FALSE (was 'completed')"),
                id="not_of_a_true_comparison",
            ),
            pytest.param(
                "!steps.a.stdout == false",
                {"steps.a.stdout": ""},
                Evaluation(False, True, True, False, "FALSE (was True)"),
                id="not_binds_tighter",
            ),
            pytest.param(
                "steps.a.stdout || steps.b.stdout",
                {"steps.a.stdout": "", "steps.b.stdout": "ok"},
                Evaluation(True, True, "ok", None, "TRUE"),
                id="lone_operands_in_or",
            ),
        ],
    )
    def test_logical_expression_reports_the_deciding_comparison(
        self, source: str, values: Mapping[str, object], expected: Evaluation
    ) -> None:
        assert _evaluate(source, values) == expected

    @pytest.mark.parametrize(
        ("source", "values", "expected"),
        [
            pytest.param(f"{_S} in {_XY}", {_S: "y"}, Evaluation(True, True, "y", ["x", "y"], "TRUE"), id="in_hit"),
            pytest.param(
                f"{_S} in {_XY}",
                {_S: "z"},
                Evaluation(False, True, "z", ["x", "y"], "FALSE (was 'z')"),
                id="in_miss",
            ),
            pytest.param(
                f"{_S} in [1, 3]", {_S: "3"}, Evaluation(True, True, "3", [1, 3], "TRUE"), id="numeric_string_in"
            ),
            pytest.param(
                f"{_S} not in {_XY}",
                {_S: "z"},
                Evaluation(True, True, "z", ["x", "y"], "TRUE"),
                id="not_in_hit",
            ),
            pytest.param(
                f"{_S} not in {_XY}",
                {_S: "x"},
                Evaluation(False, True, "x", ["x", "y"], "FALSE (was 'x')"),
                id="not_in_miss",
            ),
            pytest.param(
                f"{_S} not in [3]",
                {_S: "3"},
                Evaluation(False, True, "3", [3], "FALSE (was '3')"),
                id="numeric_string_not_in",
            ),
            pytest.param(
                f"{_S} in {_XY}",
                {},
                Evaluation(False, False, None, ["x", "y"], "FALSE (not found)"),
                id="in_absent",
            ),
            pytest.param(
                f"{_S} not in {_XY}",
                {},
                Evaluation(False, False, None, ["x", "y"], "FALSE (not found)"),
                id="not_in_absent",
            ),
            pytest.param(
                f'!({_S} in ["x"])',
                {},
                Evaluation(True, False, None, ["x"], "TRUE"),
                id="not_of_an_absent_in",
            ),
            pytest.param(
                f'{_S} not in ["x"]',
                {_S: None},
                Evaluation(False, True, None, ["x"], "FALSE (not found)"),
                id="null_not_in",
            ),
            pytest.param(
                f"{_S} in [done]",
                {_S: "done", "done": "done"},
                Evaluation(True, True, "done", ["done"], "TRUE"),
                id="bare_word_item",
            ),
        ],
    )
    def test_membership(self, source: str, values: Mapping[str, object], expected: Evaluation) -> None:
        assert _evaluate(source, values) == expected

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            pytest.param(_X_AND_Y, "n", id="and_left_false"),
            pytest.param(_X_OR_Y, "x", id="or_left_true"),
        ],
    )
    def test_short_circuit_leaves_the_right_side_unread(self, source: str, value: str) -> None:
        expression = parse_expression(source)
        assert not isinstance(expression, InvalidExpression)
        resolver = _RecordingResolver({"steps.a.stdout": value, "steps.b.stdout": "y"})

        evaluate_expression(expression, resolver)

        assert resolver.read == ["steps.a.stdout"]

    @pytest.mark.parametrize(
        ("source", "values", "expected"),
        [
            pytest.param(_A_OR_NONE, {_A: "note"}, "note", id="or_takes_truthy_left"),
            pytest.param(_A_OR_NONE, {_A: ""}, "none", id="or_skips_empty_left"),
            pytest.param(_A_OR_NONE, {}, "none", id="or_skips_absent_left"),
            pytest.param(_A_OR_NONE, {_A: "false"}, "false", id="or_keeps_string_false"),
            pytest.param("steps.a.stdout || steps.b.stdout", {}, ABSENT, id="or_both_absent"),
            pytest.param("steps.a.stdout && 'x'", {_A: "y"}, "x", id="and_takes_right"),
            pytest.param("steps.a.stdout && 'x'", {_A: 0}, 0, id="and_keeps_falsy_left"),
            pytest.param("steps.a.stdout == 'x'", {_A: "x"}, True, id="comparison"),
            pytest.param("!steps.a.stdout", {_A: "x"}, False, id="negation"),
            pytest.param(_A, {}, ABSENT, id="absent_path"),
            pytest.param("'lit'", {}, "lit", id="literal"),
        ],
    )
    def test_evaluate_value_returns_the_deciding_value(
        self, source: str, values: Mapping[str, object], expected: object
    ) -> None:
        expression = parse_expression(source)
        assert not isinstance(expression, InvalidExpression)

        assert evaluate_value(expression, _MappingResolver(values)) == expected


def _value(source: str, values: Mapping[str, object]) -> object:
    expression = parse_expression(source)
    assert not isinstance(expression, InvalidExpression)
    return evaluate_value(expression, _MappingResolver(values))


_LENGTH = "length(steps.a.stdout)"
_CONTAINS_B = "contains(steps.a.stdout, 'b')"
_JOIN = "join(steps.a.stdout)"
_FROM_JSON = "fromJSON(steps.a.stdout)"


class ExpressionFunctionTests:
    """A call returns its function's value, ABSENT on a wrong argument type, and decides as a lone operand does."""

    @pytest.mark.parametrize(
        ("source", "value", "expected"),
        [
            pytest.param(_FROM_JSON, '{"n": 1}', {"n": 1}, id="from_json_object"),
            pytest.param(_FROM_JSON, "not json", ABSENT, id="from_json_invalid"),
            pytest.param(_LENGTH, "abc", 3, id="length_string"),
            pytest.param(_LENGTH, [1, 2], 2, id="length_list"),
            pytest.param(_LENGTH, {"a": 1, "b": 2}, 2, id="length_object"),
            pytest.param(_CONTAINS_B, "abc", True, id="contains_substring"),
            pytest.param(_CONTAINS_B, ["a", "b"], True, id="contains_item"),
            pytest.param(_CONTAINS_B, ["a"], False, id="contains_missing_item"),
            pytest.param(_JOIN, ["a", "b"], "a,b", id="join_default"),
            pytest.param("join(steps.a.stdout, ' | ')", ["a", "b"], "a | b", id="join_separator"),
            pytest.param(_JOIN, [1, True, None, {"k": 1}], '1,true,,{"k": 1}', id="join_renders_items"),
            pytest.param("trim(steps.a.stdout)", "  x \n", "x", id="trim"),
            pytest.param("length(fromJSON(steps.a.stdout))", "[1, 2, 3]", 3, id="nested"),
        ],
    )
    def test_function_returns_its_value(self, source: str, value: object, expected: object) -> None:
        assert _value(source, {_A: value}) == expected

    @pytest.mark.parametrize(
        ("source", "values"),
        [
            pytest.param(_LENGTH, {_A: 5}, id="length_of_number"),
            pytest.param(_LENGTH, {_A: None}, id="length_of_null"),
            pytest.param(_JOIN, {_A: "a,b"}, id="join_of_string"),
            pytest.param("join(steps.a.stdout, 1)", {_A: ["a"]}, id="join_non_string_separator"),
            pytest.param("trim(steps.a.stdout)", {_A: ["x"]}, id="trim_of_list"),
            pytest.param("contains(steps.a.stdout, 'x')", {}, id="contains_of_absent"),
            pytest.param("contains(steps.a.stdout, 1)", {_A: "a1"}, id="contains_string_with_number"),
            pytest.param("contains(steps.a.stdout, 'a')", {_A: {"a": 1}}, id="contains_object"),
            pytest.param(_FROM_JSON, {_A: 5}, id="from_json_of_number"),
        ],
    )
    def test_wrong_argument_type_is_absent(self, source: str, values: Mapping[str, object]) -> None:
        assert _value(source, values) is ABSENT
        assert _evaluate(source, values) == Evaluation(False, False, None, None, "FALSE (not found)")

    @pytest.mark.parametrize(
        ("source", "value", "expected"),
        [
            pytest.param(_LENGTH, "abc", Evaluation(True, True, 3, None, "TRUE"), id="truthy"),
            pytest.param(
                "length(steps.a.stdout) > 2",
                5,
                Evaluation(False, False, None, 2, "FALSE (not found)"),
                id="compared_wrong_type",
            ),
        ],
    )
    def test_call_decides_like_a_lone_operand(self, source: str, value: object, expected: Evaluation) -> None:
        assert _evaluate(source, {_A: value}) == expected
