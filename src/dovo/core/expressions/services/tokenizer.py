"""Split an expression source into tokens."""

from __future__ import annotations

import re
from typing import Final

from dovo.core.expressions.models import ComparisonOperator, InvalidExpression, Token, TokenKind

_QUOTES: Final[frozenset[str]] = frozenset({"'", '"'})
_ESCAPES: Final[frozenset[str]] = frozenset({"\\", "'", '"'})
# Longest first, so '<=' is read before '<'.
_SYMBOL_OPERATORS: Final[tuple[str, ...]] = ("<=", ">=", "==", "!=", "<", ">")
_RUN_STOPS: Final[tuple[str, ...]] = ("==", "!=", "<", ">")
NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_UNSUPPORTED_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]|[+\-.][0-9]")
_ROOT_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")


def tokenize(source: str) -> tuple[Token, ...] | InvalidExpression:
    """Split source into tokens, or describe the first character that cannot start one."""
    tokens: list[Token] = []
    position = _skip_whitespace(source, 0)
    while position < len(source):
        token = _read_token(source, position)
        if isinstance(token, InvalidExpression):
            return token
        tokens.append(token)
        position = _skip_whitespace(source, token.column - 1 + len(token.text))
    return tuple(tokens)


def _skip_whitespace(source: str, position: int) -> int:
    """Return the index of the first character at or after position that is not whitespace."""
    while position < len(source) and source[position].isspace():
        position += 1
    return position


def _read_token(source: str, position: int) -> Token | InvalidExpression:
    """Read the token that starts at position."""
    if source[position] in _QUOTES:
        return _read_string(source, position)
    operator = _match_operator(source, position)
    if operator is not None:
        return Token(TokenKind.OPERATOR, operator.value, position + 1, operator)
    if source[position] == "=":
        return _unexpected_character(source, position)
    return _read_run(source, position)


def _match_operator(source: str, position: int) -> ComparisonOperator | None:
    """Return the symbol operator that starts at position, if any."""
    for symbol in _SYMBOL_OPERATORS:
        if source.startswith(symbol, position):
            return ComparisonOperator(symbol)
    return None


def _read_string(source: str, start: int) -> Token | InvalidExpression:
    """Read the quoted string whose opening quote is at start."""
    quoted = _read_quoted(source, start)
    if isinstance(quoted, InvalidExpression):
        return quoted
    value, end = quoted
    return Token(TokenKind.STRING, source[start:end], start + 1, value)


def _read_quoted(source: str, start: int) -> tuple[str, int] | InvalidExpression:
    """Return the unescaped text of the quoted string at start and the index after its closing quote."""
    quote = source[start]
    chars: list[str] = []
    position = start + 1
    while position < len(source):
        char = source[position]
        if char == quote:
            return "".join(chars), position + 1
        if char == "\\":
            escaped = source[position + 1 : position + 2]
            if escaped and escaped not in _ESCAPES:
                return InvalidExpression(source, f"unsupported escape '\\{escaped}' at column {position + 1}")
            char = escaped
            position += 1
        chars.append(char)
        position += 1
    return InvalidExpression(source, f"unterminated string at column {start + 1}")


def _read_run(source: str, start: int) -> Token | InvalidExpression:
    """Read the run of characters at start and classify it as a number, a name or a word."""
    name = _read_name(source, start)
    if isinstance(name, InvalidExpression):
        return name
    parts, name_end = name
    end = _run_end(source, name_end)
    text = source[start:end]
    column = start + 1
    if NUMBER_RE.fullmatch(text):
        return Token(TokenKind.NUMBER, text, column, float(text) if "." in text else int(text))
    if parts and name_end == end:
        return _name_token(parts, text, column)
    if _UNSUPPORTED_NUMBER_RE.match(text):
        return InvalidExpression(source, f"unsupported number '{text}' at column {column}")
    if len(parts) > 1:
        return _unexpected_character(source, name_end)
    return Token(TokenKind.WORD, text, column, text)


def _read_name(source: str, start: int) -> tuple[tuple[str, ...], int] | InvalidExpression:
    """Return the parts of the name at start and the index where it stops; no parts when no name starts there."""
    root = _ROOT_RE.match(source, start)
    if root is None:
        return (), start
    parts = [root.group()]
    position = root.end()
    while (suffix := _read_suffix(source, position)) is not None:
        if isinstance(suffix, InvalidExpression):
            return suffix
        segment, position = suffix
        parts.append(segment)
    return tuple(parts), position


def _read_suffix(source: str, position: int) -> tuple[str, int] | InvalidExpression | None:
    """Return the '.segment' or quoted bracket key at position and the index after it, if one starts there."""
    if source.startswith(".", position):
        segment = _SEGMENT_RE.match(source, position + 1)
        return None if segment is None else (segment.group(), segment.end())
    if not source.startswith("[", position) or source[position + 1 : position + 2] not in _QUOTES:
        return None
    key = _read_quoted(source, position + 1)
    if isinstance(key, InvalidExpression):
        return key
    value, end = key
    if not source.startswith("]", end):
        return _unexpected_character(source, position)
    return value, end + 1


def _run_end(source: str, position: int) -> int:
    """Return the index where the run that reaches position ends."""
    while position < len(source) and not _stops_run(source, position):
        position += 1
    return position


def _stops_run(source: str, position: int) -> bool:
    """Return whether the character at position ends a run: whitespace, a quote, '<', '>', '==' or '!='."""
    char = source[position]
    return char.isspace() or char in _QUOTES or source.startswith(_RUN_STOPS, position)


def _name_token(parts: tuple[str, ...], text: str, column: int) -> Token:
    """Return the token of a run that is a whole name; a lone 'contains' is the operator."""
    if parts == (ComparisonOperator.CONTAINS.value,):
        return Token(TokenKind.OPERATOR, text, column, ComparisonOperator.CONTAINS)
    return Token(TokenKind.NAME, text, column, parts)


def _unexpected_character(source: str, position: int) -> InvalidExpression:
    """Describe the character at position as unexpected."""
    return InvalidExpression(source, f"unexpected character '{source[position]}' at column {position + 1}")
