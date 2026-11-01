"""Tests for splitting an expression source into tokens."""

from __future__ import annotations

from dovo.core.expressions import ComparisonOperator, Token, TokenKind, tokenize


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
