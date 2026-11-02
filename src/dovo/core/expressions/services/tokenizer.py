"""Split an expression source into tokens."""

from __future__ import annotations

import re
from typing import Final

from dovo.core.expressions.models import ComparisonOperator, InvalidExpression, Token, TokenKind

_QUOTES: Final[frozenset[str]] = frozenset({"'", '"'})
_ESCAPES: Final[frozenset[str]] = frozenset({"\\", "'", '"'})
# Longest first, so '<=' is read before '<'.
_SYMBOL_OPERATORS: Final[tuple[str, ...]] = ("<=", ">=", "==", "!=", "<", ">")
# Longest first, so '&&' is read whole; a '!' here is never followed by '=', which reads as '!=' first.
_SYMBOLS: Final[tuple[str, ...]] = ("&&", "||", "(", ")", "[", "]", ",", "!")
_RUN_STOPS: Final[tuple[str, ...]] = ("==", "!=", "<", ">", "&&", "||", "(", ")", "[", "]", ",")
# A run that is one of these lone names is the operator, not a name.
_NAME_OPERATORS: Final[dict[tuple[str, ...], ComparisonOperator]] = {
    (ComparisonOperator.CONTAINS.value,): ComparisonOperator.CONTAINS,
    (ComparisonOperator.IN.value,): ComparisonOperator.IN,
}
_NOT: Final[tuple[str, ...]] = ("not",)
NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_UNSUPPORTED_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]|[+\-.][0-9]")
_ROOT_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")
_INDEX_RE: Final[re.Pattern[str]] = re.compile(r"\[(-?[0-9]+)\]")


def tokenize(source: str) -> tuple[Token, ...] | InvalidExpression:
    """Split source into tokens, or describe the first character that cannot start one."""
    tokens: list[Token] = []
    position = _skip_whitespace(source, 0)
    while position < len(source):
        token = _read_token(source, position, tokens[-1] if tokens else None)
        if isinstance(token, InvalidExpression):
            return token
        tokens.append(token)
        position = _skip_whitespace(source, _token_end(token))
    return tuple(tokens)


def _token_end(token: Token) -> int:
    """Return the index just after token's text."""
    return token.column - 1 + len(token.text)


def _skip_whitespace(source: str, position: int) -> int:
    """Return the index of the first character at or after position that is not whitespace."""
    while position < len(source) and source[position].isspace():
        position += 1
    return position


def _read_token(source: str, position: int, previous: Token | None) -> Token | InvalidExpression:
    """Read the token that starts at position; a projection only straight after the name token previous."""
    if _follows_name(previous, position) and _starts_projection(source, position):
        return _read_projection(source, position)
    if source[position] in _QUOTES:
        return _read_string(source, position)
    operator = _match_operator(source, position)
    if operator is not None:
        return Token(TokenKind.OPERATOR, operator.value, position + 1, operator)
    symbol = next((symbol for symbol in _SYMBOLS if source.startswith(symbol, position)), None)
    if symbol is not None:
        return Token(TokenKind.SYMBOL, symbol, position + 1, symbol)
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


def _follows_name(previous: Token | None, position: int) -> bool:
    """Return whether previous is a name token that ends exactly at position."""
    return previous is not None and previous.kind is TokenKind.NAME and _token_end(previous) == position


def _starts_projection(source: str, position: int) -> bool:
    """Return whether a '.*' at position is followed straight away by a '.<segment>' or a quoted bracket key."""
    if not source.startswith(".*", position):
        return False
    after = position + 2
    if source.startswith(".", after):
        return _SEGMENT_RE.match(source, after + 1) is not None
    return source.startswith("[", after) and source[after + 1 : after + 2] in _QUOTES


def _read_projection(source: str, start: int) -> Token | InvalidExpression:
    """Read the projection whose '.*' is at start, with every '.<segment>' and quoted bracket key after it.

    An unquoted integer bracket index ends the keys, and a run that goes on past them is an unexpected character.
    """
    keys: list[str] = []
    position = start + 2
    while (suffix := _read_suffix(source, position)) is not None:
        if isinstance(suffix, InvalidExpression):
            return suffix
        key, end = suffix
        if isinstance(key, int):
            break
        keys.append(key)
        position = end
    if not _ends_projection(source, position):
        return _unexpected_character(source, position)
    return Token(TokenKind.PROJECTION, source[start:position], start + 1, tuple(keys))


def _ends_projection(source: str, position: int) -> bool:
    """Return whether a projection's keys may end at position: at the end of source, or where a run stops but '['."""
    if position == len(source):
        return True
    return not source.startswith("[", position) and _stops_run(source, position)


def _read_run(source: str, start: int) -> Token | InvalidExpression:
    """Read the run of characters at start and classify it as a number, a name or a word.

    A name stops before a projection, which the next token reads.
    """
    name = _read_name(source, start)
    if isinstance(name, InvalidExpression):
        return name
    parts, name_end = name
    end = name_end if parts and _starts_projection(source, name_end) else _run_end(source, name_end)
    text = source[start:end]
    column = start + 1
    if NUMBER_RE.fullmatch(text):
        return Token(TokenKind.NUMBER, text, column, float(text) if "." in text else int(text))
    if parts and name_end == end:
        return _name_token(source, parts, start, end)
    if _UNSUPPORTED_NUMBER_RE.match(text):
        return InvalidExpression(source, f"unsupported number '{text}' at column {column}")
    if len(parts) > 1:
        return _unexpected_character(source, name_end)
    return Token(TokenKind.WORD, text, column, text)


def _read_name(source: str, start: int) -> tuple[tuple[str | int, ...], int] | InvalidExpression:
    """Return the parts of the name at start and the index where it stops; no parts when no name starts there.

    An unquoted bracket index is an int part. A root of exactly 'in' reads no bracket key or index, so 'in["x"]' and
    'in[0]' are the operator 'in' and a list.
    """
    root = _ROOT_RE.match(source, start)
    if root is None:
        return (), start
    parts: list[str | int] = [root.group()]
    position = root.end()
    if parts == [ComparisonOperator.IN.value] and source.startswith("[", position):
        return tuple(parts), position
    while (suffix := _read_suffix(source, position)) is not None:
        if isinstance(suffix, InvalidExpression):
            return suffix
        segment, position = suffix
        parts.append(segment)
    return tuple(parts), position


def _read_suffix(source: str, position: int) -> tuple[str | int, int] | InvalidExpression | None:
    """Return the '.segment', quoted bracket key or integer bracket index at position and the index after it.

    None when no suffix starts there, so anything else after '[', spaces or a word included, ends the name.
    """
    if source.startswith(".", position):
        segment = _SEGMENT_RE.match(source, position + 1)
        return None if segment is None else (segment.group(), segment.end())
    if not source.startswith("[", position):
        return None
    if source[position + 1 : position + 2] in _QUOTES:
        return _read_bracket_key(source, position)
    index = _INDEX_RE.match(source, position)
    return None if index is None else (int(index.group(1)), index.end())


def _read_bracket_key(source: str, position: int) -> tuple[str, int] | InvalidExpression:
    """Return the quoted key of the bracket that opens at position and the index after its ']'."""
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
    """Return whether the character at position ends a run: whitespace, a quote, an operator or a symbol but '!'.

    The stops are '<', '>', '==', '!=', '&&', '||', '(', ')', '[', ']' and ','; a '!' not followed by '=', a lone
    '&' and a lone '|' stay inside a run.
    """
    char = source[position]
    return char.isspace() or char in _QUOTES or source.startswith(_RUN_STOPS, position)


def _name_token(source: str, parts: tuple[str | int, ...], start: int, end: int) -> Token:
    """Return the token of the run from start to end, which is a whole name.

    A lone 'contains' or 'in' is the operator, and a lone 'not' followed, after whitespace only, by the operator 'in'
    is one 'not in' operator token whose text runs from 'not' to 'in'.
    """
    text = source[start:end]
    operator = _NAME_OPERATORS.get(parts)
    if operator is not None:
        return Token(TokenKind.OPERATOR, text, start + 1, operator)
    not_in = _read_not_in(source, start, end) if parts == _NOT else None
    return not_in or Token(TokenKind.NAME, text, start + 1, parts)


def _read_not_in(source: str, start: int, end: int) -> Token | None:
    """Return the 'not in' token whose 'not' runs from start to end, when the next run is a lone 'in'.

    The lookahead matches the run without reading a token, so a run of 'not' words is never re-read.
    """
    following = _skip_whitespace(source, end)
    word = _ROOT_RE.match(source, following)
    if word is None or word.group() != ComparisonOperator.IN.value:
        return None
    if word.end() < len(source) and not _stops_run(source, word.end()):
        return None
    return Token(TokenKind.OPERATOR, source[start : word.end()], start + 1, ComparisonOperator.NOT_IN)


def _unexpected_character(source: str, position: int) -> InvalidExpression:
    """Describe the character at position as unexpected."""
    return InvalidExpression(source, f"unexpected character '{source[position]}' at column {position + 1}")
