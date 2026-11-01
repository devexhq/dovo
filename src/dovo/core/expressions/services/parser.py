"""Parse an expression source into its syntax tree.

expression := and ("||" and)*; and := comparison ("&&" comparison)*; comparison := unary [<operator> unary];
unary := "!" unary | primary; primary := operand | "(" expression ")".
"""

from __future__ import annotations

from collections.abc import Callable

from dovo.core.expressions.models import (
    Comparison,
    ComparisonOperator,
    Expression,
    InvalidExpression,
    Literal,
    Logical,
    LogicalOperator,
    Negation,
    Operand,
    Path,
    Token,
    TokenKind,
)
from dovo.core.expressions.services.tokenizer import tokenize

_BOOLEANS: dict[str, bool] = {"true": True, "false": False}

_Parsed = Expression | InvalidExpression


def parse_expression(source: str) -> Expression | InvalidExpression:
    """Parse source into its syntax tree, or describe the first reason it is not an expression.

    '!' binds tighter than a comparison, a comparison tighter than '&&', and '&&' tighter than '||'; '&&' and '||'
    group to the left, parentheses leave no node, and a comparison does not chain.
    """
    if not source.strip():
        return InvalidExpression(source, "empty expression")
    tokens = tokenize(source)
    if isinstance(tokens, InvalidExpression):
        return tokens
    return _unquoted_text(source, tokens) or _Parser(source, tokens).parse()


class _Parser:
    """A recursive-descent parser over one source's tokens, one method per precedence level."""

    def __init__(self, source: str, tokens: tuple[Token, ...]) -> None:
        """Start at the first of tokens, which tokenize read from source."""
        self._source = source
        self._tokens = tokens
        self._index = 0

    def parse(self) -> _Parsed:
        """Parse every token as one expression; a token left over is unexpected."""
        expression = self._or()
        token = self._peek()
        if isinstance(expression, InvalidExpression) or token is None:
            return expression
        return self._unexpected(token)

    def _or(self) -> _Parsed:
        """Parse 'and' levels joined by '||'."""
        return self._logical(LogicalOperator.OR, self._and)

    def _and(self) -> _Parsed:
        """Parse comparisons joined by '&&'."""
        return self._logical(LogicalOperator.AND, self._comparison)

    def _logical(self, operator: LogicalOperator, operand: Callable[[], _Parsed]) -> _Parsed:
        """Parse operands joined by operator, grouped to the left."""
        left = operand()
        while not isinstance(left, InvalidExpression) and self._at_symbol(operator.value):
            self._index += 1
            right = operand()
            if isinstance(right, InvalidExpression):
                return right
            left = Logical(left, operator, right)
        return left

    def _comparison(self) -> _Parsed:
        """Parse one unary, compared with a second when a comparison operator follows it."""
        left = self._unary()
        token = self._peek()
        if isinstance(left, InvalidExpression) or token is None or token.kind is not TokenKind.OPERATOR:
            return left
        self._index += 1
        right = self._unary()
        if isinstance(right, InvalidExpression):
            return right
        return Comparison(left, ComparisonOperator(token.text), right)

    def _unary(self) -> _Parsed:
        """Parse any number of '!' before a primary."""
        if not self._at_symbol("!"):
            return self._primary()
        self._index += 1
        operand = self._unary()
        return operand if isinstance(operand, InvalidExpression) else Negation(operand)

    def _primary(self) -> _Parsed:
        """Parse an operand or a parenthesised expression."""
        token = self._peek()
        if token is None:
            return InvalidExpression(self._source, f"missing value after '{self._tokens[self._index - 1].text}'")
        if self._at_symbol("("):
            return self._group(token)
        if token.kind in (TokenKind.OPERATOR, TokenKind.SYMBOL):
            return self._unexpected(token)
        self._index += 1
        return _operand(token)

    def _group(self, opening: Token) -> _Parsed:
        """Parse the expression inside the '(' at opening and its closing ')'."""
        self._index += 1
        inner = self._or()
        if isinstance(inner, InvalidExpression):
            return inner
        if self._at_symbol(")"):
            self._index += 1
            return inner
        token = self._peek()
        if token is not None:
            return self._unexpected(token)
        return InvalidExpression(self._source, f"missing ')' to close '(' at column {opening.column}")

    def _peek(self) -> Token | None:
        """Return the next token, or None when every token is read."""
        return self._tokens[self._index] if self._index < len(self._tokens) else None

    def _at_symbol(self, text: str) -> bool:
        """Return whether the next token is the symbol text."""
        token = self._peek()
        return token is not None and token.kind is TokenKind.SYMBOL and token.text == text

    def _unexpected(self, token: Token) -> InvalidExpression:
        """Describe token as one that cannot stand where it is."""
        return InvalidExpression(self._source, f"unexpected '{token.text}' at column {token.column}")


def _unquoted_text(source: str, tokens: tuple[Token, ...]) -> InvalidExpression | None:
    """Describe the first run of two or more bare words, which needs quotes to be one value."""
    words: list[Token] = []
    for token in tokens:
        if _is_bare_word(token):
            words.append(token)
        elif len(words) > 1:
            break
        else:
            words = []
    if len(words) < 2:
        return None
    text = " ".join(word.text for word in words)
    return InvalidExpression(
        source, f"unquoted text '{text}' at column {words[0].column}; quote a value of more than one word"
    )


def _is_bare_word(token: Token) -> bool:
    """Return whether token is a word or a lone name."""
    return token.kind is TokenKind.WORD or (isinstance(token.value, tuple) and len(token.value) == 1)


def _operand(token: Token) -> Operand:
    """Return the Path or Literal an operand token stands for; a lone 'true' or 'false', in any case, is a boolean."""
    match token.value:
        case (str() as root,) if root.lower() in _BOOLEANS:
            return Literal(_BOOLEANS[root.lower()], token.text)
        case (str() as root, *segments):
            return Path(root, tuple(str(segment) for segment in segments), token.text)
        case str() | int() | float() as value:
            return Literal(value, token.text)
    raise TypeError(f"Token {token.text!r} of kind {token.kind} is not an operand")
