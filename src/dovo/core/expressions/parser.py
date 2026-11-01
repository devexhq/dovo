"""Parse an expression source into one comparison or one operand."""

from __future__ import annotations

from dovo.core.expressions.models import (
    Comparison,
    ComparisonOperator,
    Expression,
    InvalidExpression,
    Literal,
    Operand,
    Path,
    Token,
    TokenKind,
)
from dovo.core.expressions.tokenizer import tokenize

_BOOLEANS: dict[str, bool] = {"true": True, "false": False}


def parse_expression(source: str) -> Expression | InvalidExpression:
    """Parse one comparison or one operand, or describe why source is not one."""
    if not source.strip():
        return InvalidExpression(source, "empty expression")
    tokens = tokenize(source)
    if isinstance(tokens, InvalidExpression):
        return tokens
    failure = _unquoted_text(source, tokens) or _missing_value(source, tokens) or _unexpected_token(source, tokens)
    if failure is not None:
        return failure
    left = _operand(tokens[0])
    if len(tokens) == 1:
        return left
    return Comparison(left, ComparisonOperator(tokens[1].text), _operand(tokens[2]))


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


def _missing_value(source: str, tokens: tuple[Token, ...]) -> InvalidExpression | None:
    """Describe an operator that ends the source."""
    if tokens[-1].kind is not TokenKind.OPERATOR:
        return None
    return InvalidExpression(source, f"missing value after '{tokens[-1].text}'")


def _unexpected_token(source: str, tokens: tuple[Token, ...]) -> InvalidExpression | None:
    """Describe the first token that cannot stand where it is in '<operand> [<operator> <operand>]'."""
    for index, token in enumerate(tokens):
        if index > 2 or (token.kind is TokenKind.OPERATOR) != (index == 1):
            return InvalidExpression(source, f"unexpected '{token.text}' at column {token.column}")
    return None


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
