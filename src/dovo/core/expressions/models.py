"""Tokens and the syntax tree of one expression, the reason a source is not one, and how one evaluated."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Final, Protocol


class TokenKind(StrEnum):
    """What one token of an expression source is."""

    STRING = "string"
    NUMBER = "number"
    NAME = "name"
    WORD = "word"
    OPERATOR = "operator"
    SYMBOL = "symbol"
    PROJECTION = "projection"


@dataclass(frozen=True, slots=True)
class Token:
    """One token: its kind, its source text, its 1-based column and its value.

    The value is the unescaped text of a string, the int or float of a number, the (root, *segments) tuple of a
    name (each segment a str, or an int for an unquoted bracket index such as '[0]'), the text of a word, the ComparisonOperator of an operator, the text of a symbol: '&&', '||', '!', '(',
    ')', '[', ']' or ',', or the tuple of string keys of a projection such as '.*.name'.
    """

    kind: TokenKind
    text: str
    column: int
    value: object


class ComparisonOperator(StrEnum):
    """An operator that compares two operands."""

    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    CONTAINS = "contains"
    IN = "in"
    NOT_IN = "not in"


@dataclass(frozen=True, slots=True)
class Literal:
    """A string, number or boolean value, with its source text."""

    value: str | int | float | bool
    text: str


@dataclass(frozen=True, slots=True)
class Path:
    """A name read from a namespace: its root, its segments and its source text.

    A bracket key is one segment, so 'steps.a.outputs["x.y"]' has root 'steps' and segments ('a', 'outputs', 'x.y').
    An unquoted bracket index is an int segment, so 'steps[-1].status' has segments (-1, 'status') while
    "steps['0'].status" has ('0', 'status').
    """

    root: str
    segments: tuple[str | int, ...]  # an int is an unquoted bracket index: 'steps[-1].status' -> (-1, 'status')
    text: str


Operand = Literal | Path


class LogicalOperator(StrEnum):
    """An operator that combines two expressions."""

    AND = "&&"
    OR = "||"


@dataclass(frozen=True, slots=True)
class Negation:
    """'!' applied to an expression."""

    operand: Expression


@dataclass(frozen=True, slots=True)
class Logical:
    """Two expressions joined by '&&' or '||'."""

    left: Expression
    operator: LogicalOperator
    right: Expression


@dataclass(frozen=True, slots=True)
class ValueList:
    """The bracketed items after 'in' or 'not in'."""

    items: tuple[Operand, ...]


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two expressions and the operator comparing them; each side is an operand, a call, a projection, a negation or a parenthesised one.

    The right side is a ValueList exactly when the operator is 'in' or 'not in'.
    """

    left: Expression
    operator: ComparisonOperator
    right: Expression | ValueList


@dataclass(frozen=True, slots=True)
class Call:
    """A known function applied to its arguments, as in 'length(steps.a.stdout)'."""

    name: str
    arguments: tuple[Expression, ...]


@dataclass(frozen=True, slots=True)
class Projection:
    """'<array>.*.<key>': the path of the array, the keys read from each of its objects, and the source text."""

    base: Path
    keys: tuple[str, ...]
    text: str


Expression = Logical | Negation | Comparison | Call | Projection | Operand


@dataclass(frozen=True, slots=True)
class InvalidExpression:
    """A source that is not an expression, and why."""

    source: str
    reason: str

    @property
    def message(self) -> str:
        """Return the message `Invalid expression '<source>': <reason>.`, naming the source and the reason."""
        return f"Invalid expression '{self.source}': {self.reason}."


class Absent(Enum):
    """The value of a path that names nothing, as opposed to a JSON null."""

    ABSENT = "absent"


ABSENT: Final = Absent.ABSENT


class Resolver(Protocol):
    """Supplies the value of each path an expression reads."""

    def resolve(self, path: Path) -> object:
        """Return the value path names, or ABSENT when it names none."""
        ...


@dataclass(frozen=True, slots=True)
class Evaluation:
    """How one expression evaluated.

    - passed is the truthiness of the expression's value.
    - resolved, actual and expected come from the comparison or lone operand that decided it: for '&&' and '||' the
      side whose value the expression took, and for '!' its operand's. A call or a projection decides as a lone operand does.
    - resolved is False when a path that comparison read was ABSENT, or when a call's value is ABSENT, a wrong
      argument type included, so a negated unresolved comparison passes unresolved.
    - actual is the left (or lone) operand's value, None when absent.
    - expected is the right operand's value, or the list of a ValueList's item values, each None when absent; it is
      also None for a lone operand.
    - detail is 'TRUE', 'FALSE (not found)' when actual is None, else "FALSE (was <actual!r>)".
    """

    passed: bool
    resolved: bool
    actual: object
    expected: object
    detail: str
