"""Evaluate an expression: a comparison, or a lone operand by its truthiness."""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Final, TypeIs

from dovo.core.expressions.models import (
    ABSENT,
    Comparison,
    ComparisonOperator,
    Evaluation,
    Expression,
    Literal,
    Operand,
    Resolver,
)
from dovo.core.expressions.services.tokenizer import NUMBER_RE

_NUMERIC_OPERATORS: Final[dict[ComparisonOperator, Callable[[float, float], bool]]] = {
    ComparisonOperator.GE: operator.ge,
    ComparisonOperator.LE: operator.le,
    ComparisonOperator.GT: operator.gt,
    ComparisonOperator.LT: operator.lt,
    ComparisonOperator.EQ: operator.eq,
    ComparisonOperator.NE: operator.ne,
}
_EQUALITY_OPERATORS: Final[dict[ComparisonOperator, Callable[[object, object], bool]]] = {
    ComparisonOperator.EQ: operator.eq,
    ComparisonOperator.NE: operator.ne,
}


def evaluate_expression(expression: Expression, resolver: Resolver) -> Evaluation:
    """Evaluate a comparison, or a lone operand by its truthiness, reading each path through resolver."""
    if isinstance(expression, Comparison):
        return _evaluate_comparison(expression, resolver)
    value = _operand_value(expression, resolver)
    resolved = value is not ABSENT
    actual = None if value is ABSENT else value
    passed = resolved and is_truthy(value)
    return Evaluation(passed, resolved, actual, None, _detail(passed, actual))


def is_truthy(value: object) -> bool:
    """Return False for False, 0, 0.0, '', None and ABSENT, True for every other value, '0', 'false' and '[]' included."""
    if value is None or value is ABSENT:
        return False
    if isinstance(value, (bool, int, float, str)):
        return bool(value)
    return True


def _evaluate_comparison(comparison: Comparison, resolver: Resolver) -> Evaluation:
    """Compare both operands of comparison; an absent operand leaves it unresolved and false."""
    left = _operand_value(comparison.left, resolver)
    right = _operand_value(comparison.right, resolver)
    resolved = left is not ABSENT and right is not ABSENT
    actual = None if left is ABSENT else left
    expected = None if right is ABSENT else right
    passed = resolved and _compare_values(actual, expected, comparison.operator)
    return Evaluation(passed, resolved, actual, expected, _detail(passed, actual))


def _operand_value(operand: Operand, resolver: Resolver) -> object:
    """Return a literal's value, or what resolver reads for a path."""
    if isinstance(operand, Literal):
        return operand.value
    return resolver.resolve(operand)


def _compare_values(actual: object, expected: object, comparison_operator: ComparisonOperator) -> bool:
    """Compare actual with expected under comparison_operator; a None on either side is false for every operator."""
    if actual is None or expected is None:
        return False
    if comparison_operator is ComparisonOperator.CONTAINS:
        return _compare_contains(actual, expected)
    numeric = _numeric_operands(actual, expected)
    if numeric is not None:
        return bool(_NUMERIC_OPERATORS[comparison_operator](*numeric))
    return _compare_equality(actual, expected, comparison_operator)


def _compare_equality(actual: object, expected: object, comparison_operator: ComparisonOperator) -> bool:
    """Compare two operands that are not both numbers; only '==' and '!=' compare them, every ordering is false."""
    equality_fn = _EQUALITY_OPERATORS.get(comparison_operator)
    return bool(equality_fn(actual, expected)) if equality_fn else False


def _compare_contains(actual: object, expected: object) -> bool:
    """Check whether actual holds expected: a key of a dict, an item of a list, set or tuple, else a substring."""
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, (list, dict, set, tuple)):
        return expected in actual
    return str(expected) in str(actual)


def _numeric_operands(actual: object, expected: object) -> tuple[int | float, int | float] | None:
    """Return both operands as numbers when both are numbers or one is a number and the other a numeric string."""
    if _is_numeric(actual) and _is_numeric(expected):
        return actual, expected
    if _is_numeric(actual):
        other = _numeric_string(expected)
        return None if other is None else (actual, other)
    if _is_numeric(expected):
        other = _numeric_string(actual)
        return None if other is None else (other, expected)
    return None


def _numeric_string(value: object) -> int | float | None:
    """Convert a string that is an integer or a decimal to its number, or None for any other value."""
    if not isinstance(value, str) or NUMBER_RE.fullmatch(value) is None:
        return None
    return float(value) if "." in value else int(value)


def _is_numeric(value: object) -> TypeIs[int | float]:
    """Check whether a value is numeric (int or float, excluding bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _detail(passed: bool, actual: object) -> str:
    """Return 'TRUE', 'FALSE (not found)' when actual is None, else "FALSE (was <actual!r>)"."""
    if passed:
        return "TRUE"
    if actual is None:
        return "FALSE (not found)"
    return f"FALSE (was {actual!r})"
