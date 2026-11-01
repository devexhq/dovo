"""Condition expression evaluator shared by loop 'until' and step 'when' lists."""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from typing import Final, TypeIs

from dovo.core.catalog.definitions import parse_condition_expression, parse_literal
from dovo.core.inputs import resolve_step_output
from dovo.engine.executors.models import ConditionEvaluationResult, StepResult

_NUMERIC_COMPARISON_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}
_STRING_COMPARISON_OPERATORS: dict[str, Callable[[object, object], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
}


def _is_numeric(val: object) -> TypeIs[int | float]:
    """Check whether a value is numeric (int or float, excluding bool)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


_NUMERIC_STRING_RE: Final = re.compile(r"-?[0-9]+(\.[0-9]+)?")
"""An integer or a decimal written as a string; compared numerically against a number."""


def _numeric_string(value: object) -> int | float | None:
    """Convert a string that is an integer or a decimal to its number, or None for any other value."""
    if not isinstance(value, str) or _NUMERIC_STRING_RE.fullmatch(value) is None:
        return None
    return float(value) if "." in value else int(value)


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


_UNRESOLVED: Final = object()
"""Marks an operand that references a step value which does not exist, as opposed to a JSON null."""


def _resolve_step_field(result: StepResult, field: str, subpath: list[str]) -> object:
    """Extract a step attribute or its `outputs.<key>[.<path>]` value from a StepResult, or _UNRESOLVED when it has none."""
    if field in ("exit_code", "status", "stdout"):
        return getattr(result, field)
    if field == "outputs" and subpath:
        found = resolve_step_output(result.outputs, result.stdout, subpath[0], subpath[1:])
        return _UNRESOLVED if found is None else found.value
    return _UNRESOLVED


def _operand_value(operand: str, *, iteration_index: int, step_results: dict[str, StepResult] | None) -> object:
    """Resolve an operand value against iteration index and step results, or _UNRESOLVED for a missing step value."""
    token = operand.strip()
    if token in ("iteration.index", "iteration.attempt", "iteration"):
        return iteration_index

    if token.startswith("steps."):
        parts = token.split(".")
        if len(parts) >= 3:
            step_res = (step_results or {}).get(parts[1])
            if step_res is None:
                return _UNRESOLVED
            return _resolve_step_field(step_res, parts[2], parts[3:])

    return parse_literal(token)


def _compare_contains(actual: object, expected: object) -> bool:
    """Check if expected element or substring is contained within actual value."""
    if actual is None or expected is None:
        return False
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, (list, dict, set, tuple)):
        return expected in actual
    return str(expected) in str(actual)


def _compare_values(actual: object, expected: object, operator: str) -> bool:
    """Evaluate comparison between actual and expected values under operator."""
    if actual is None or expected is None:
        return False
    if operator == "contains":
        return _compare_contains(actual, expected)
    numeric = _numeric_operands(actual, expected)
    if numeric is not None:
        numeric_fn = _NUMERIC_COMPARISON_OPERATORS.get(operator)
        return bool(numeric_fn(*numeric)) if numeric_fn else False
    string_fn = _STRING_COMPARISON_OPERATORS.get(operator)
    return bool(string_fn(actual, expected)) if string_fn else False


def _format_condition_detail(passed: bool, actual: object) -> str:
    """Format diagnostic string explaining condition evaluation status."""
    if passed:
        return "TRUE"
    if actual is None:
        return "FALSE (not found)"
    return f"FALSE (was {actual!r})"


def evaluate_condition(
    expression: str,
    *,
    iteration_index: int = 1,
    step_results: dict[str, StepResult] | None = None,
) -> ConditionEvaluationResult:
    """Parse and evaluate a single condition expression."""
    parsed = parse_condition_expression(expression)
    if parsed is None:
        return ConditionEvaluationResult(
            expression=expression,
            passed=False,
            actual=None,
            expected=None,
            detail="FALSE (syntax error)",
        )

    left = _operand_value(parsed.left, iteration_index=iteration_index, step_results=step_results)
    right = _operand_value(parsed.right, iteration_index=iteration_index, step_results=step_results)
    resolved = left is not _UNRESOLVED and right is not _UNRESOLVED
    actual = None if left is _UNRESOLVED else left
    expected = None if right is _UNRESOLVED else right
    passed = resolved and _compare_values(actual, expected, parsed.operator)
    detail = _format_condition_detail(passed, actual)

    return ConditionEvaluationResult(
        expression=expression,
        passed=passed,
        resolved=resolved,
        actual=actual,
        expected=expected,
        detail=detail,
    )
