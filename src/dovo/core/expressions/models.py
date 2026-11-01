"""Tokens and the syntax tree of one expression, and the reason a source is not one."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TokenKind(StrEnum):
    """What one token of an expression source is."""

    STRING = "string"
    NUMBER = "number"
    NAME = "name"
    WORD = "word"
    OPERATOR = "operator"


@dataclass(frozen=True, slots=True)
class Token:
    """One token: its kind, its source text, its 1-based column and its value.

    The value is the unescaped text of a string, the int or float of a number, the (root, *segments) tuple of a
    name, the text of a word, or the ComparisonOperator of an operator.
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


@dataclass(frozen=True, slots=True)
class Literal:
    """A string, number or boolean value, with its source text."""

    value: str | int | float | bool
    text: str


@dataclass(frozen=True, slots=True)
class Path:
    """A name read from a namespace: its root, its segments and its source text.

    A bracket key is one segment, so 'steps.a.outputs["x.y"]' has root 'steps' and segments ('a', 'outputs', 'x.y').
    """

    root: str
    segments: tuple[str, ...]
    text: str


Operand = Literal | Path


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two operands and the operator comparing them."""

    left: Operand
    operator: ComparisonOperator
    right: Operand


Expression = Comparison | Operand


@dataclass(frozen=True, slots=True)
class InvalidExpression:
    """A source that is not an expression, and why."""

    source: str
    reason: str

    @property
    def message(self) -> str:
        """Return the message `Invalid expression '<source>': <reason>.`, naming the source and the reason."""
        return f"Invalid expression '{self.source}': {self.reason}."
