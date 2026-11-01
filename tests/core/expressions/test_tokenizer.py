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
