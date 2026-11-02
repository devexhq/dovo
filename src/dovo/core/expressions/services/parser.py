"""Parse an expression source into its syntax tree.

expression := and ("||" and)*; and := comparison ("&&" comparison)*;
comparison := unary [<operator> unary | ("in" | "not in") list]; list := "[" [operand ("," operand)*] "]";
unary := "!" unary | primary; primary := call | projection | operand | "(" expression ")";
call := name "(" [expression ("," expression)*] ")"; projection := path ".*" key+.
"""

from __future__ import annotations

from collections.abc import Callable

from dovo.core.expressions.models import (
    Call,
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
    Projection,
    Token,
    TokenKind,
    ValueList,
)
from dovo.core.expressions.services.functions import KNOWN_FUNCTIONS, function_arity
from dovo.core.expressions.services.tokenizer import tokenize

_BOOLEANS: dict[str, bool] = {"true": True, "false": False}
_MEMBERSHIP_OPERATORS: frozenset[ComparisonOperator] = frozenset({ComparisonOperator.IN, ComparisonOperator.NOT_IN})
# The deepest run of '(' and '!' a source may nest, and the most '&&' and '||' it may hold in all, which together
# keep every tree well inside the interpreter's recursion limit.
_MAX_NESTING = 32
_MAX_JOINS = 64
_KNOWN_FUNCTIONS_TEXT = ", ".join(KNOWN_FUNCTIONS)

_Parsed = Expression | InvalidExpression


def parse_expression(source: str) -> Expression | InvalidExpression:
    """Parse source into its syntax tree, or describe the first reason it is not an expression.

    '!' binds tighter than a comparison, a comparison tighter than '&&', and '&&' tighter than '||'; '&&' and '||'
    group to the left, parentheses leave no node, and a comparison does not chain. '(' and '!' nest at most 32 deep,
    and a source holds at most 64 '&&' and '||' in all.
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
        self._depth = 0
        self._joins = 0

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
        """Parse operands joined by operator, grouped to the left, unless the source joins more than _MAX_JOINS."""
        left = operand()
        while not isinstance(left, InvalidExpression) and self._at_symbol(operator.value):
            if self._joins == _MAX_JOINS:
                return self._too_many_joins()
            self._index += 1
            self._joins += 1
            right = operand()
            if isinstance(right, InvalidExpression):
                return right
            left = Logical(left, operator, right)
        return left

    def _comparison(self) -> _Parsed:
        """Parse one unary, compared with a second, or with a list after 'in' and 'not in', when an operator follows."""
        left = self._unary()
        token = self._peek()
        if isinstance(left, InvalidExpression) or token is None or token.kind is not TokenKind.OPERATOR:
            return left
        self._index += 1
        operator = ComparisonOperator(token.value)
        right = self._list(token) if operator in _MEMBERSHIP_OPERATORS else self._unary()
        if isinstance(right, InvalidExpression):
            return right
        return Comparison(left, operator, right)

    def _list(self, operator: Token) -> ValueList | InvalidExpression:
        """Parse the bracketed list of operands after the operator 'in' or 'not in'."""
        opening = self._peek()
        if opening is None:
            return InvalidExpression(self._source, f"missing value after '{operator.text}'")
        if not self._at_symbol("["):
            return InvalidExpression(
                self._source, f"expected a list after '{operator.text}' at column {opening.column}"
            )
        self._index += 1
        items = () if self._at_symbol("]") else self._items(opening)
        if isinstance(items, InvalidExpression):
            return items
        return self._close_list(opening, items)

    def _items(self, opening: Token) -> tuple[Operand, ...] | InvalidExpression:
        """Parse one or more operands separated by ',' inside the list opened at opening."""
        items: list[Operand] = []
        while True:
            item = self._item(opening)
            if isinstance(item, InvalidExpression):
                return item
            items.append(item)
            if not self._at_symbol(","):
                return tuple(items)
            self._index += 1

    def _item(self, opening: Token) -> Operand | InvalidExpression:
        """Parse one list item, which is any operand."""
        token = self._peek()
        if token is None:
            return _unclosed_list(self._source, opening)
        if token.kind in (TokenKind.OPERATOR, TokenKind.SYMBOL):
            return self._unexpected(token)
        self._index += 1
        return _operand(token)

    def _close_list(self, opening: Token, items: tuple[Operand, ...]) -> ValueList | InvalidExpression:
        """Read the ']' that closes the list opened at opening."""
        if self._at_symbol("]"):
            self._index += 1
            return ValueList(items)
        token = self._peek()
        return _unclosed_list(self._source, opening) if token is None else self._unexpected(token)

    def _unary(self) -> _Parsed:
        """Parse any number of '!' before a primary."""
        token = self._peek()
        if token is None or not self._at_symbol("!"):
            return self._primary()
        operand = self._nested(token, self._unary)
        return operand if isinstance(operand, InvalidExpression) else Negation(operand)

    def _primary(self) -> _Parsed:
        """Parse a call, a projection, an operand or a parenthesised expression."""
        token = self._peek()
        if token is None:
            return InvalidExpression(self._source, f"missing value after '{self._tokens[self._index - 1].text}'")
        if self._at_symbol("("):
            return self._group(token)
        if self._starts_call(token):
            return self._call(token)
        if token.kind in (TokenKind.OPERATOR, TokenKind.SYMBOL):
            return self._unexpected(token)
        self._index += 1
        return self._projection(_operand(token))

    def _projection(self, operand: Operand) -> _Parsed:
        """Return operand, or its Projection when a projection token follows; projecting a literal is unexpected."""
        token = self._peek()
        if token is None or token.kind is not TokenKind.PROJECTION:
            return operand
        if not isinstance(operand, Path) or not isinstance(token.value, tuple):
            return self._unexpected(token)
        self._index += 1
        return Projection(operand, tuple(str(key) for key in token.value), operand.text + token.text)

    def _starts_call(self, token: Token) -> bool:
        """Return whether token, standing where an operand stands, is a one-part name or 'contains' before a '('."""
        is_name = token.kind is TokenKind.NAME and isinstance(token.value, tuple) and len(token.value) == 1
        is_contains = token.kind is TokenKind.OPERATOR and token.value is ComparisonOperator.CONTAINS
        following = self._index + 1
        return (
            (is_name or is_contains)
            and following < len(self._tokens)
            and self._tokens[following].kind is TokenKind.SYMBOL
            and self._tokens[following].text == "("
        )

    def _call(self, name: Token) -> _Parsed:
        """Parse the call of the function name names: its parenthesised arguments, checked against its arity."""
        arity = function_arity(name.text)
        if arity is None:
            return InvalidExpression(
                self._source,
                f"unknown function '{name.text}' at column {name.column}; known functions: {_KNOWN_FUNCTIONS_TEXT}",
            )
        self._index += 1
        opening = self._tokens[self._index]
        arguments = self._nested(opening, self._arguments)
        if isinstance(arguments, InvalidExpression):
            return arguments
        unclosed = self._close(opening)
        if unclosed is not None:
            return unclosed
        fewest, most = arity
        if not fewest <= len(arguments) <= most:
            return InvalidExpression(
                self._source,
                f"function '{name.text}' at column {name.column} takes {_argument_count(fewest, most)}, "
                f"not {len(arguments)}; known functions: {_KNOWN_FUNCTIONS_TEXT}",
            )
        return Call(name.text, arguments)

    def _arguments(self) -> tuple[Expression, ...] | InvalidExpression:
        """Parse a call's arguments, expressions separated by ',', or none when ')' follows at once."""
        if self._at_symbol(")"):
            return ()
        arguments: list[Expression] = []
        while True:
            argument = self._or()
            if isinstance(argument, InvalidExpression):
                return argument
            arguments.append(argument)
            if not self._at_symbol(","):
                return tuple(arguments)
            self._index += 1

    def _nested[T](self, opening: Token, parse: Callable[[], T]) -> T | InvalidExpression:
        """Read the '(' or '!' at opening and parse what it nests, unless it nests deeper than _MAX_NESTING."""
        if self._depth == _MAX_NESTING:
            return InvalidExpression(
                self._source,
                f"'{opening.text}' at column {opening.column} nests too deeply; nest at most {_MAX_NESTING} levels",
            )
        self._index += 1
        self._depth += 1
        parsed = parse()
        self._depth -= 1
        return parsed

    def _group(self, opening: Token) -> _Parsed:
        """Parse the expression inside the '(' at opening and its closing ')'."""
        inner = self._nested(opening, self._or)
        if isinstance(inner, InvalidExpression):
            return inner
        unclosed = self._close(opening)
        return inner if unclosed is None else unclosed

    def _close(self, opening: Token) -> InvalidExpression | None:
        """Read the ')' that closes the '(' at opening, or describe why the next token is not it."""
        if self._at_symbol(")"):
            self._index += 1
            return None
        token = self._peek()
        if token is not None:
            return self._unexpected(token)
        return InvalidExpression(self._source, f"missing ')' to close '(' at column {opening.column}")

    def _too_many_joins(self) -> InvalidExpression:
        """Describe the next token, the '&&' or '||' past _MAX_JOINS, as one join too many."""
        token = self._tokens[self._index]
        return InvalidExpression(
            self._source,
            f"'{token.text}' at column {token.column} joins too many conditions; use at most {_MAX_JOINS} '&&' and '||'",
        )

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


def _argument_count(fewest: int, most: int) -> str:
    """Return how many arguments a function takes: '1 argument', '2 arguments' or '1 or 2 arguments'."""
    count = str(fewest) if fewest == most else f"{fewest} or {most}"
    return f"{count} argument" if most == 1 else f"{count} arguments"


def _unclosed_list(source: str, opening: Token) -> InvalidExpression:
    """Describe the list opened at opening as missing its ']'."""
    return InvalidExpression(source, f"missing ']' to close '[' at column {opening.column}")


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
    """Return whether token is a word or a one-part name; a projection is never a bare word."""
    if token.kind is TokenKind.WORD:
        return True
    return token.kind is TokenKind.NAME and isinstance(token.value, tuple) and len(token.value) == 1


def _operand(token: Token) -> Operand:
    """Return the Path or Literal an operand token stands for; a lone 'true' or 'false', in any case, is a boolean."""
    match token.value:
        case (str() as root,) if root.lower() in _BOOLEANS:
            return Literal(_BOOLEANS[root.lower()], token.text)
        case (str() as root, *segments):
            return Path(root, tuple(_segment(segment) for segment in segments), token.text)
        case str() | int() | float() as value:
            return Literal(value, token.text)
    raise TypeError(f"Token {token.text!r} of kind {token.kind} is not an operand")


def _segment(segment: object) -> str | int:
    """Return a name token's segment as a Path segment: an int bracket index stays an int, every other is its text."""
    return segment if isinstance(segment, int) else str(segment)
