"""Tests for splitting an expression source into tokens."""

from __future__ import annotations

from dovo.core.expressions import ComparisonOperator, InvalidExpression, Token, TokenKind, tokenize


class ExpressionTokenizerTests:
    """tokenize gives each token its kind, source text, 1-based column and value."""

    def test_tokenize_reports_kinds_and_columns(self) -> None:
        assert tokenize("  steps.a.exit_code>=-1") == (
            Token(TokenKind.NAME, "steps.a.exit_code", 3, ("steps", "a", "exit_code")),
            Token(TokenKind.OPERATOR, ">=", 20, ComparisonOperator.GE),
            Token(TokenKind.NUMBER, "-1", 22, -1),
        )
        assert tokenize("steps.a.stdout==a/b") == (
            Token(TokenKind.NAME, "steps.a.stdout", 1, ("steps", "a", "stdout")),
            Token(TokenKind.OPERATOR, "==", 15, ComparisonOperator.EQ),
            Token(TokenKind.WORD, "a/b", 17, "a/b"),
        )

    def test_logical_symbols_are_tokens(self) -> None:
        assert tokenize("!(steps.a.exit_code==0)&&x||y") == (
            Token(TokenKind.SYMBOL, "!", 1, "!"),
            Token(TokenKind.SYMBOL, "(", 2, "("),
            Token(TokenKind.NAME, "steps.a.exit_code", 3, ("steps", "a", "exit_code")),
            Token(TokenKind.OPERATOR, "==", 20, ComparisonOperator.EQ),
            Token(TokenKind.NUMBER, "0", 22, 0),
            Token(TokenKind.SYMBOL, ")", 23, ")"),
            Token(TokenKind.SYMBOL, "&&", 24, "&&"),
            Token(TokenKind.NAME, "x", 26, ("x",)),
            Token(TokenKind.SYMBOL, "||", 27, "||"),
            Token(TokenKind.NAME, "y", 29, ("y",)),
        )
        assert tokenize("ok! a&b a|b") == (
            Token(TokenKind.WORD, "ok!", 1, "ok!"),
            Token(TokenKind.WORD, "a&b", 5, "a&b"),
            Token(TokenKind.WORD, "a|b", 9, "a|b"),
        )

    def test_membership_tokens(self) -> None:
        assert tokenize('steps.a.stdout not  in ["x",1]') == (
            Token(TokenKind.NAME, "steps.a.stdout", 1, ("steps", "a", "stdout")),
            Token(TokenKind.OPERATOR, "not  in", 16, ComparisonOperator.NOT_IN),
            Token(TokenKind.SYMBOL, "[", 24, "["),
            Token(TokenKind.STRING, '"x"', 25, "x"),
            Token(TokenKind.SYMBOL, ",", 28, ","),
            Token(TokenKind.NUMBER, "1", 29, 1),
            Token(TokenKind.SYMBOL, "]", 30, "]"),
        )
        assert tokenize('steps.a.stdout in["x"]') == (
            Token(TokenKind.NAME, "steps.a.stdout", 1, ("steps", "a", "stdout")),
            Token(TokenKind.OPERATOR, "in", 16, ComparisonOperator.IN),
            Token(TokenKind.SYMBOL, "[", 18, "["),
            Token(TokenKind.STRING, '"x"', 19, "x"),
            Token(TokenKind.SYMBOL, "]", 22, "]"),
        )
        assert tokenize("steps.in.status") == (Token(TokenKind.NAME, "steps.in.status", 1, ("steps", "in", "status")),)
        assert tokenize('steps.a["in"]') == (Token(TokenKind.NAME, 'steps.a["in"]', 1, ("steps", "a", "in")),)

    def test_integer_bracket_index_is_a_name_segment(self) -> None:
        assert tokenize("steps[0].status") == (Token(TokenKind.NAME, "steps[0].status", 1, ("steps", 0, "status")),)
        assert tokenize("steps[-1].id") == (Token(TokenKind.NAME, "steps[-1].id", 1, ("steps", -1, "id")),)
        assert tokenize("steps['0'].id") == (Token(TokenKind.NAME, "steps['0'].id", 1, ("steps", "0", "id")),)
        assert tokenize("steps[ 0 ]") == (
            Token(TokenKind.NAME, "steps", 1, ("steps",)),
            Token(TokenKind.SYMBOL, "[", 6, "["),
            Token(TokenKind.NUMBER, "0", 8, 0),
            Token(TokenKind.SYMBOL, "]", 10, "]"),
        )
        assert tokenize("x in[0]") == (
            Token(TokenKind.NAME, "x", 1, ("x",)),
            Token(TokenKind.OPERATOR, "in", 3, ComparisonOperator.IN),
            Token(TokenKind.SYMBOL, "[", 5, "["),
            Token(TokenKind.NUMBER, "0", 6, 0),
            Token(TokenKind.SYMBOL, "]", 7, "]"),
        )

    def test_projection_is_one_token_after_a_name(self) -> None:
        assert tokenize("steps.a.outputs.x.*.name") == (
            Token(TokenKind.NAME, "steps.a.outputs.x", 1, ("steps", "a", "outputs", "x")),
            Token(TokenKind.PROJECTION, ".*.name", 18, ("name",)),
        )
        assert tokenize("x.*['a.b'].c") == (
            Token(TokenKind.NAME, "x", 1, ("x",)),
            Token(TokenKind.PROJECTION, ".*['a.b'].c", 2, ("a.b", "c")),
        )
        assert tokenize("steps.a.stdout.*.n[0]") == InvalidExpression(
            "steps.a.stdout.*.n[0]", "unexpected character '[' at column 19"
        )
        assert tokenize("x.*") == (Token(TokenKind.WORD, "x.*", 1, "x.*"),)
        assert tokenize("a.*b") == (Token(TokenKind.WORD, "a.*b", 1, "a.*b"),)
        assert tokenize("*.py") == (Token(TokenKind.WORD, "*.py", 1, "*.py"),)

    def test_run_of_not_words_reads_only_the_last_as_not_in(self) -> None:
        tokens = tokenize("not " * 300 + "in [1]")

        assert isinstance(tokens, tuple)
        assert tokens[:299] == tuple(Token(TokenKind.NAME, "not", 4 * index + 1, ("not",)) for index in range(299))
        assert tokens[299:] == (
            Token(TokenKind.OPERATOR, "not in", 1197, ComparisonOperator.NOT_IN),
            Token(TokenKind.SYMBOL, "[", 1204, "["),
            Token(TokenKind.NUMBER, "1", 1205, 1),
            Token(TokenKind.SYMBOL, "]", 1206, "]"),
        )
        assert tokenize("x not in.y") == (
            Token(TokenKind.NAME, "x", 1, ("x",)),
            Token(TokenKind.NAME, "not", 3, ("not",)),
            Token(TokenKind.NAME, "in.y", 7, ("in", "y")),
        )
