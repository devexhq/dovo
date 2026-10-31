"""Condition expression evaluator shared by loop 'until' and step 'when' lists."""

from __future__ import annotations

import json
import operator
from collections.abc import Callable
from typing import Final, TypeIs

from dovo.core.catalog.definitions import parse_condition_expression, parse_literal
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


_UNRESOLVED: Final = object()
"""Marks an operand that references a step value which does not exist, as opposed to a JSON null."""


def _lookup_json_path(root: object, path: list[str]) -> object:
    """Traverse nested dictionary keys along a subpath, returning _UNRESOLVED when a key is missing."""
    current = root
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return _UNRESOLVED
        current = current[segment]
    return current


def _resolve_json_path(root: object, path: list[str]) -> object:
    """Traverse nested dictionary keys along a subpath, returning None when a key is missing."""
    value = _lookup_json_path(root, path)
    return None if value is _UNRESOLVED else value


def _resolve_step_field(result: StepResult, field: str, subpath: list[str]) -> object:
    """Extract step attribute or parsed JSON output subpath from a StepResult, or _UNRESOLVED when it has none."""
    if field in ("exit_code", "status", "stdout"):
        return getattr(result, field)
    if field == "outputs":
        try:
            parsed = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return _UNRESOLVED
        return _lookup_json_path(parsed, subpath)
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


def resolve_operand_value(
    operand: str,
    *,
    iteration_index: int = 1,
    step_results: dict[str, StepResult] | None = None,
) -> object:
    """Resolve an operand value against iteration index and step results; a missing step value is None."""
    value = _operand_value(operand, iteration_index=iteration_index, step_results=step_results)
    return None if value is _UNRESOLVED else value


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
    if _is_numeric(actual) and _is_numeric(expected):
        numeric_fn = _NUMERIC_COMPARISON_OPERATORS.get(operator)
        return bool(numeric_fn(actual, expected)) if numeric_fn else False
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
