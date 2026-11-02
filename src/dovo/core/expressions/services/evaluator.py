"""Evaluate an expression by its value's truthiness, reporting the comparison or operand that decided it.

evaluate_expression reports how an expression passed or failed; evaluate_value returns the value itself.
'&&' and '||' short-circuit: the side not taken is never evaluated, so its paths are never resolved.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Iterable
from typing import Final, TypeIs

from dovo.core.expressions.models import (
    ABSENT,
    Comparison,
    ComparisonOperator,
    Evaluation,
    Expression,
    Literal,
    Logical,
    LogicalOperator,
    Negation,
    Operand,
    Resolver,
    ValueList,
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
# 'in' passes when any item compares '==', 'not in' when every item compares '!='.
_MEMBERSHIP_OPERATORS: Final[
    dict[ComparisonOperator, tuple[Callable[[Iterable[object]], bool], ComparisonOperator]]
] = {
    ComparisonOperator.IN: (any, ComparisonOperator.EQ),
    ComparisonOperator.NOT_IN: (all, ComparisonOperator.NE),
}


def evaluate_expression(expression: Expression, resolver: Resolver) -> Evaluation:
    """Evaluate expression, reading each path through resolver; it passes when its value is truthy.

    resolved, actual and expected come from the comparison or lone operand that decided the value, and detail is
    recomputed from passed and that actual.
    """
    value, deciding = _evaluate(expression, resolver)
    passed = is_truthy(value)
    return Evaluation(passed, deciding.resolved, deciding.actual, deciding.expected, _detail(passed, deciding.actual))


def evaluate_value(expression: Expression, resolver: Resolver) -> object:
    """Return expression's value, reading each path through resolver.

    A literal's value; what resolver reads for a path, ABSENT included; a comparison's or a negation's bool; and for
    '&&' and '||' the value of the side that decided it, the other side never evaluated.
    """
    return _evaluate(expression, resolver)[0]


def is_truthy(value: object) -> bool:
    """Return False for False, 0, 0.0, '', None and ABSENT, True for every other value, '0', 'false' and '[]' included."""
    if value is None or value is ABSENT:
        return False
    if isinstance(value, (bool, int, float, str)):
        return bool(value)
    return True


def _evaluate(expression: Expression, resolver: Resolver) -> tuple[object, Evaluation]:
    """Return expression's value and the Evaluation of the comparison or lone operand that decided it."""
    if isinstance(expression, Logical):
        return _evaluate_logical(expression, resolver)
    if isinstance(expression, Negation):
        value, deciding = _evaluate(expression.operand, resolver)
        return not is_truthy(value), deciding
    if isinstance(expression, Comparison):
        evaluation = _evaluate_comparison(expression, resolver)
        return evaluation.passed, evaluation
    return _evaluate_operand(expression, resolver)


def _evaluate_logical(logical: Logical, resolver: Resolver) -> tuple[object, Evaluation]:
    """Take the left side when it decides ('&&' falsy, '||' truthy) without evaluating the right, else the right."""
    left = _evaluate(logical.left, resolver)
    if is_truthy(left[0]) is (logical.operator is LogicalOperator.OR):
        return left
    return _evaluate(logical.right, resolver)


def _evaluate_operand(operand: Operand, resolver: Resolver) -> tuple[object, Evaluation]:
    """Return a lone operand's value and its Evaluation by truthiness; an absent path is unresolved."""
    value = _operand_value(operand, resolver)
    resolved = value is not ABSENT
    actual = value if resolved else None
    passed = is_truthy(value)
    return value, Evaluation(passed, resolved, actual, None, _detail(passed, actual))


def _evaluate_comparison(comparison: Comparison, resolver: Resolver) -> Evaluation:
    """Compare the values of both sides of comparison; an absent value leaves it unresolved and false."""
    left, _ = _evaluate(comparison.left, resolver)
    right = _right_value(comparison.right, resolver)
    resolved = left is not ABSENT and right is not ABSENT
    actual = None if left is ABSENT else left
    expected = None if right is ABSENT else right
    passed = resolved and _compare(actual, expected, comparison.operator)
    return Evaluation(passed, resolved, actual, expected, _detail(passed, actual))


def _right_value(right: Expression | ValueList, resolver: Resolver) -> object:
    """Return the right side's value: a ValueList's item values, or ABSENT when any item is ABSENT."""
    if not isinstance(right, ValueList):
        return _evaluate(right, resolver)[0]
    values = [_operand_value(item, resolver) for item in right.items]
    return ABSENT if any(value is ABSENT for value in values) else values


def _compare(actual: object, expected: object, comparison_operator: ComparisonOperator) -> bool:
    """Compare actual with expected; 'in' and 'not in' compare actual with each item of the expected list."""
    membership = _MEMBERSHIP_OPERATORS.get(comparison_operator)
    if membership is None or not isinstance(expected, list):
        return _compare_values(actual, expected, comparison_operator)
    quantifier, item_operator = membership
    return quantifier(_compare_values(actual, item, item_operator) for item in expected)


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
