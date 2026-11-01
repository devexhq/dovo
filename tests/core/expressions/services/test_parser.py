"""Tests for parsing an expression source into its syntax tree or the reason it is not an expression."""

from __future__ import annotations

import pytest

from dovo.core.expressions import (
    Comparison,
    ComparisonOperator,
    Expression,
    InvalidExpression,
    Literal,
    Logical,
    LogicalOperator,
    Negation,
    Path,
    ValueList,
    parse_expression,
)

STDOUT = Path("steps", ("a", "stdout"), "steps.a.stdout")
EXIT_CODE = Path("steps", ("a", "exit_code"), "steps.a.exit_code")
A = Comparison(EXIT_CODE, ComparisonOperator.EQ, Literal(0, "0"))
B = Comparison(Path("steps", ("b", "exit_code"), "steps.b.exit_code"), ComparisonOperator.EQ, Literal(0, "0"))
C = Comparison(Path("steps", ("c", "exit_code"), "steps.c.exit_code"), ComparisonOperator.EQ, Literal(0, "0"))
AND = LogicalOperator.AND
OR = LogicalOperator.OR


def _negated(expression: Expression, times: int) -> Expression:
    """Return expression wrapped in times negations."""
    for _ in range(times):
        expression = Negation(expression)
    return expression


class ExpressionParserTests:
    """parse_expression reads comparisons, operands, '!', '&&', '||' and parentheses and names why a source is invalid."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(
                "steps.run-tests.exit_code == 0",
                Comparison(
                    Path("steps", ("run-tests", "exit_code"), "steps.run-tests.exit_code"),
                    ComparisonOperator.EQ,
                    Literal(0, "0"),
                ),
                id="eq",
            ),
            pytest.param(
                "steps.a.exit_code != 1",
                Comparison(EXIT_CODE, ComparisonOperator.NE, Literal(1, "1")),
                id="ne",
            ),
            pytest.param(
                "steps.a.exit_code < 2",
                Comparison(EXIT_CODE, ComparisonOperator.LT, Literal(2, "2")),
                id="lt",
            ),
            pytest.param(
                "steps.a.exit_code <= 2",
                Comparison(EXIT_CODE, ComparisonOperator.LE, Literal(2, "2")),
                id="le",
            ),
            pytest.param(
                "steps.a.exit_code > 2",
                Comparison(EXIT_CODE, ComparisonOperator.GT, Literal(2, "2")),
                id="gt",
            ),
            pytest.param(
                "steps.a.exit_code >= 2",
                Comparison(EXIT_CODE, ComparisonOperator.GE, Literal(2, "2")),
                id="ge",
            ),
            pytest.param(
                "steps.build.stdout contains success",
                Comparison(
                    Path("steps", ("build", "stdout"), "steps.build.stdout"),
                    ComparisonOperator.CONTAINS,
                    Path("success", (), "success"),
                ),
                id="contains_bare_word",
            ),
            pytest.param(
                'steps.a.stdout == "needs edits"',
                Comparison(STDOUT, ComparisonOperator.EQ, Literal("needs edits", '"needs edits"')),
                id="double_quoted",
            ),
            pytest.param(
                "steps.a.stdout == 'needs edits'",
                Comparison(STDOUT, ComparisonOperator.EQ, Literal("needs edits", "'needs edits'")),
                id="single_quoted",
            ),
            pytest.param(
                r"steps.a.stdout == 'it\'s \"done\" \\ ok'",
                Comparison(STDOUT, ComparisonOperator.EQ, Literal('it\'s "done" \\ ok', r"'it\'s \"done\" \\ ok'")),
                id="escaped_quote",
            ),
            pytest.param(
                "steps.a.exit_code == 42",
                Comparison(EXIT_CODE, ComparisonOperator.EQ, Literal(42, "42")),
                id="integer",
            ),
            pytest.param(
                "steps.a.exit_code == -1",
                Comparison(EXIT_CODE, ComparisonOperator.EQ, Literal(-1, "-1")),
                id="negative_integer",
            ),
            pytest.param(
                "steps.a.stdout >= 0.75",
                Comparison(STDOUT, ComparisonOperator.GE, Literal(0.75, "0.75")),
                id="decimal",
            ),
            pytest.param(
                "steps.a.outputs['x.y'] == TRUE",
                Comparison(
                    Path("steps", ("a", "outputs", "x.y"), "steps.a.outputs['x.y']"),
                    ComparisonOperator.EQ,
                    Literal(True, "TRUE"),
                ),
                id="true_any_case",
            ),
            pytest.param(
                "steps.a.stdout != fAlSe",
                Comparison(STDOUT, ComparisonOperator.NE, Literal(False, "fAlSe")),
                id="false_any_case",
            ),
            pytest.param(
                "steps.run-tests.status == completed",
                Comparison(
                    Path("steps", ("run-tests", "status"), "steps.run-tests.status"),
                    ComparisonOperator.EQ,
                    Path("completed", (), "completed"),
                ),
                id="hyphenated_step_id",
            ),
            pytest.param(
                'steps.a.outputs["review verdict"] == approve',
                Comparison(
                    Path("steps", ("a", "outputs", "review verdict"), 'steps.a.outputs["review verdict"]'),
                    ComparisonOperator.EQ,
                    Path("approve", (), "approve"),
                ),
                id="bracket_key",
            ),
            pytest.param(
                "3 < steps.a.exit_code",
                Comparison(Literal(3, "3"), ComparisonOperator.LT, EXIT_CODE),
                id="number_on_the_left",
            ),
            pytest.param(
                "steps.a.exit_code>=-1",
                Comparison(EXIT_CODE, ComparisonOperator.GE, Literal(-1, "-1")),
                id="no_spaces",
            ),
        ],
    )
    def test_comparison_parses(self, source: str, expected: Comparison) -> None:
        assert parse_expression(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param("steps.a.exit_code", EXIT_CODE, id="path"),
            pytest.param("done", Path("done", (), "done"), id="bare_word"),
            pytest.param("'x'", Literal("x", "'x'"), id="quoted_string"),
            pytest.param("3", Literal(3, "3"), id="number"),
            pytest.param("False", Literal(False, "False"), id="boolean"),
        ],
    )
    def test_lone_operand_parses(self, source: str, expected: Expression) -> None:
        assert parse_expression(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(
                "steps.a.exit_code == 0 && steps.b.exit_code == 0 || steps.c.exit_code == 0",
                Logical(Logical(A, AND, B), OR, C),
                id="and_before_or",
            ),
            pytest.param(
                "steps.a.exit_code == 0 || steps.b.exit_code == 0 && steps.c.exit_code == 0",
                Logical(A, OR, Logical(B, AND, C)),
                id="or_then_and",
            ),
            pytest.param(
                "(steps.a.exit_code == 0 || steps.b.exit_code == 0) && steps.c.exit_code == 0",
                Logical(Logical(A, OR, B), AND, C),
                id="parentheses_group",
            ),
            pytest.param(
                "steps.a.exit_code == 0 && steps.b.exit_code == 0 && steps.c.exit_code == 0",
                Logical(Logical(A, AND, B), AND, C),
                id="left_associative_and",
            ),
            pytest.param(
                "!steps.a.stdout == false",
                Comparison(Negation(STDOUT), ComparisonOperator.EQ, Literal(False, "false")),
                id="not_binds_tighter_than_comparison",
            ),
            pytest.param("!(steps.a.exit_code == 0)", Negation(A), id="not_of_a_group"),
            pytest.param("!!steps.a.stdout", Negation(Negation(STDOUT)), id="double_not"),
            pytest.param("steps.a.exit_code==0&&steps.a.stdout", Logical(A, AND, STDOUT), id="no_spaces"),
            pytest.param(
                "steps.a.exit_code == (1)",
                Comparison(EXIT_CODE, ComparisonOperator.EQ, Literal(1, "1")),
                id="parenthesised_number",
            ),
        ],
    )
    def test_logical_expression_parses(self, source: str, expected: Expression) -> None:
        assert parse_expression(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(
                "steps.a.stdout in [\"x\", 'y']",
                Comparison(STDOUT, ComparisonOperator.IN, ValueList((Literal("x", '"x"'), Literal("y", "'y'")))),
                id="in_strings",
            ),
            pytest.param(
                "steps.a.stdout not in [1, true, done]",
                Comparison(
                    STDOUT,
                    ComparisonOperator.NOT_IN,
                    ValueList((Literal(1, "1"), Literal(True, "true"), Path("done", (), "done"))),
                ),
                id="not_in_mixed",
            ),
            pytest.param(
                "steps.a.stdout in []",
                Comparison(STDOUT, ComparisonOperator.IN, ValueList(())),
                id="empty_list",
            ),
            pytest.param(
                "steps.a.stdout in [steps.b.stdout]",
                Comparison(
                    STDOUT, ComparisonOperator.IN, ValueList((Path("steps", ("b", "stdout"), "steps.b.stdout"),))
                ),
                id="path_item",
            ),
            pytest.param(
                'steps.a.stdout in ["x"] && steps.a.exit_code == 0',
                Logical(Comparison(STDOUT, ComparisonOperator.IN, ValueList((Literal("x", '"x"'),))), AND, A),
                id="binds_tighter_than_and",
            ),
            pytest.param(
                'steps.in.status == "x"',
                Comparison(
                    Path("steps", ("in", "status"), "steps.in.status"), ComparisonOperator.EQ, Literal("x", '"x"')
                ),
                id="step_named_in",
            ),
        ],
    )
    def test_membership_parses(self, source: str, expected: Expression) -> None:
        assert parse_expression(source) == expected

    @pytest.mark.parametrize(
        "word",
        [
            pytest.param("a/b", id="slash"),
            pytest.param("ok!", id="bang"),
            pytest.param("key:value", id="colon"),
            pytest.param("key=value", id="single_equals"),
            pytest.param("done.", id="trailing_dot"),
            pytest.param("café", id="non_ascii"),
        ],
    )
    def test_punctuated_bare_word_parses_as_a_string(self, word: str) -> None:
        assert parse_expression(f"steps.a.stdout == {word}") == Comparison(
            STDOUT, ComparisonOperator.EQ, Literal(word, word)
        )

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            pytest.param("", "Invalid expression '': empty expression.", id="empty"),
            pytest.param("   ", "Invalid expression '   ': empty expression.", id="whitespace_only"),
            pytest.param(
                "steps.a.exit_code ==",
                "Invalid expression 'steps.a.exit_code ==': missing value after '=='.",
                id="dangling_operator",
            ),
            pytest.param(
                'steps.a.stdout == "abc',
                "Invalid expression 'steps.a.stdout == \"abc': unterminated string at column 19.",
                id="unterminated_string",
            ),
            pytest.param(
                r"steps.a.stdout == 'a\nb'",
                r"Invalid expression 'steps.a.stdout == 'a\nb'': unsupported escape '\n' at column 21.",
                id="unsupported_escape",
            ),
            pytest.param(
                "steps.a.status == needs edits",
                "Invalid expression 'steps.a.status == needs edits': "
                "unquoted text 'needs edits' at column 19; quote a value of more than one word.",
                id="multi_word_bare_value",
            ),
            pytest.param(
                "steps.a.exit_code == == 0",
                "Invalid expression 'steps.a.exit_code == == 0': unexpected '==' at column 22.",
                id="doubled_operator",
            ),
            pytest.param(
                "steps.a.stdout == ab<cd",
                "Invalid expression 'steps.a.stdout == ab<cd': unexpected '<' at column 21.",
                id="operator_inside_a_word",
            ),
            pytest.param(
                "== steps.a.exit_code",
                "Invalid expression '== steps.a.exit_code': unexpected '==' at column 1.",
                id="leading_operator",
            ),
            pytest.param(
                "steps.a.exit_code == 0 1",
                "Invalid expression 'steps.a.exit_code == 0 1': unexpected '1' at column 24.",
                id="value_after_comparison",
            ),
            pytest.param(
                "steps.a.stdout contains <none>",
                "Invalid expression 'steps.a.stdout contains <none>': unexpected '<' at column 25.",
                id="misplaced_operator_before_trailing_operator",
            ),
            pytest.param(
                'steps.a.stdout == "x"contains',
                "Invalid expression 'steps.a.stdout == \"x\"contains': unexpected 'contains' at column 22.",
                id="trailing_operator_after_comparison",
            ),
            pytest.param(
                "steps.a.exit_code = 0",
                "Invalid expression 'steps.a.exit_code = 0': unexpected character '=' at column 19.",
                id="lone_equals",
            ),
            pytest.param(
                "steps.a.stdout == 1e3",
                "Invalid expression 'steps.a.stdout == 1e3': unsupported number '1e3' at column 19.",
                id="exponent_number",
            ),
            pytest.param(
                "steps.a.stdout == +1",
                "Invalid expression 'steps.a.stdout == +1': unsupported number '+1' at column 19.",
                id="sign_led_number",
            ),
            pytest.param(
                "steps.a.stdout == 2024-01-01",
                "Invalid expression 'steps.a.stdout == 2024-01-01': unsupported number '2024-01-01' at column 19.",
                id="digit_leading_word",
            ),
            pytest.param(
                "steps.a.stdout == a.b/c",
                "Invalid expression 'steps.a.stdout == a.b/c': unexpected character '/' at column 22.",
                id="dotted_name_with_more",
            ),
            pytest.param(
                "steps.a.outputs['x' == 1",
                "Invalid expression 'steps.a.outputs['x' == 1': unexpected character '[' at column 16.",
                id="unclosed_bracket",
            ),
            pytest.param("!", "Invalid expression '!': missing value after '!'.", id="lone_not"),
            pytest.param(
                "steps.a.exit_code == 0 &&",
                "Invalid expression 'steps.a.exit_code == 0 &&': missing value after '&&'.",
                id="dangling_and",
            ),
            pytest.param(
                "(steps.a.exit_code == 0",
                "Invalid expression '(steps.a.exit_code == 0': missing ')' to close '(' at column 1.",
                id="unclosed_parenthesis",
            ),
            pytest.param(
                "steps.a.exit_code == 0)",
                "Invalid expression 'steps.a.exit_code == 0)': unexpected ')' at column 23.",
                id="unmatched_parenthesis",
            ),
            pytest.param("()", "Invalid expression '()': unexpected ')' at column 2.", id="empty_parentheses"),
            pytest.param(
                "steps.a.exit_code == 0 == 1",
                "Invalid expression 'steps.a.exit_code == 0 == 1': unexpected '==' at column 24.",
                id="chained_comparison",
            ),
            pytest.param(
                "steps.a.stdout == f(x)",
                "Invalid expression 'steps.a.stdout == f(x)': unexpected '(' at column 20.",
                id="call_syntax",
            ),
            pytest.param(
                'steps.a.stdout in "x"',
                "Invalid expression 'steps.a.stdout in \"x\"': expected a list after 'in' at column 19.",
                id="in_without_list",
            ),
            pytest.param(
                "steps.a.stdout in",
                "Invalid expression 'steps.a.stdout in': missing value after 'in'.",
                id="dangling_in",
            ),
            pytest.param(
                'steps.a.stdout in ["x"',
                "Invalid expression 'steps.a.stdout in [\"x\"': missing ']' to close '[' at column 19.",
                id="unclosed_list",
            ),
            pytest.param(
                'steps.a.stdout in ["x",]',
                "Invalid expression 'steps.a.stdout in [\"x\",]': unexpected ']' at column 24.",
                id="trailing_comma",
            ),
            pytest.param(
                "steps.a.stdout in [!x]",
                "Invalid expression 'steps.a.stdout in [!x]': unexpected '!' at column 20.",
                id="negation_item",
            ),
            pytest.param(
                'steps.a.stdout == ["x"]',
                "Invalid expression 'steps.a.stdout == [\"x\"]': unexpected '[' at column 19.",
                id="list_after_equals",
            ),
            pytest.param(
                "steps.a.stdout in [needs edits]",
                "Invalid expression 'steps.a.stdout in [needs edits]': "
                "unquoted text 'needs edits' at column 20; quote a value of more than one word.",
                id="unquoted_item_text",
            ),
            pytest.param(
                "steps.a.stdout == a,b",
                "Invalid expression 'steps.a.stdout == a,b': unexpected ',' at column 20.",
                id="comma_in_a_word",
            ),
            pytest.param(
                "steps.a.stdout == in",
                "Invalid expression 'steps.a.stdout == in': unexpected 'in' at column 19.",
                id="lone_in_as_value",
            ),
        ],
    )
    def test_invalid_source_reports_its_reason(self, source: str, message: str) -> None:
        result = parse_expression(source)

        assert isinstance(result, InvalidExpression)
        assert result.message == message

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param("(" * 32 + "steps.a.exit_code == 0" + ")" * 32, A, id="parentheses"),
            pytest.param("!" * 32 + "steps.a.stdout", _negated(STDOUT, 32), id="negations"),
            pytest.param("(!" * 16 + "steps.a.stdout" + ")" * 16, _negated(STDOUT, 16), id="mixed"),
        ],
    )
    def test_nesting_at_the_limit_parses(self, source: str, expected: Expression) -> None:
        assert parse_expression(source) == expected

    @pytest.mark.parametrize(
        ("source", "reason"),
        [
            pytest.param(
                "(" * 200 + "steps.a.exit_code == 0" + ")" * 200,
                "'(' at column 33 nests too deeply; nest at most 32 levels",
                id="parentheses",
            ),
            pytest.param(
                "!" * 1000 + "steps.a.stdout",
                "'!' at column 33 nests too deeply; nest at most 32 levels",
                id="negations",
            ),
            pytest.param(
                "(!" * 17 + "steps.a.stdout" + ")" * 17,
                "'(' at column 33 nests too deeply; nest at most 32 levels",
                id="mixed",
            ),
        ],
    )
    def test_nesting_past_the_limit_reports_its_reason(self, source: str, reason: str) -> None:
        result = parse_expression(source)

        assert result == InvalidExpression(source, reason)
