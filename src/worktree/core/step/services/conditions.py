"""Condition expression evaluator for loop 'until' clauses."""

from __future__ import annotations

import json
import operator
from typing import Any

from worktree.core.catalog.definitions import parse_condition_expression, parse_literal
from worktree.core.step.models import ConditionEvaluationResult, StepResult

_NUMERIC_COMPARISON_OPERATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}
_STRING_COMPARISON_OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
}


def _is_numeric(val: Any) -> bool:
    """Check whether a value is numeric (int or float, excluding bool)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _resolve_json_path(root: Any, path: list[str]) -> Any:
    """Traverse nested dictionary keys along a subpath."""
    current = root
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _resolve_step_field(result: StepResult, field: str, subpath: list[str]) -> Any:
    """Extract step attribute or parsed JSON output subpath from a StepResult."""
    if field in ("exit_code", "status", "stdout"):
        return getattr(result, field)
    if field == "outputs":
        try:
            parsed = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return None
        if not subpath:
            return parsed
        return _resolve_json_path(parsed, subpath)
    return None


def resolve_operand_value(
    operand: str,
    *,
    iteration_index: int = 1,
    step_results: dict[str, StepResult] | None = None,
) -> Any:
    """Resolve an operand value against iteration index and step results."""
    token = operand.strip()
    if token in ("iteration.index", "iteration.attempt", "iteration"):
        return iteration_index

    if token.startswith("steps."):
        parts = token.split(".")
        if len(parts) >= 3:
            step_id = parts[1]
            field = parts[2]
            subpath = parts[3:]
            results = step_results or {}
            step_res = results.get(step_id)
            if step_res is None:
                return None
            return _resolve_step_field(step_res, field, subpath)

    return parse_literal(token)


def _compare_contains(actual: Any, expected: Any) -> bool:
    """Check if expected element or substring is contained within actual value."""
    if actual is None or expected is None:
        return False
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, (list, dict, set, tuple)):
        return expected in actual
    return str(expected) in str(actual)


def _compare_values(actual: Any, expected: Any, operator: str) -> bool:
    """Evaluate comparison between actual and expected values under operator."""
    if actual is None or expected is None:
        return False
    if operator == "contains":
        return _compare_contains(actual, expected)
    if _is_numeric(actual) and _is_numeric(expected):
        comparison_fn = _NUMERIC_COMPARISON_OPERATORS.get(operator)
    else:
        comparison_fn = _STRING_COMPARISON_OPERATORS.get(operator)
    return bool(comparison_fn(actual, expected)) if comparison_fn else False


def _format_condition_detail(passed: bool, actual: Any) -> str:
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

    actual = resolve_operand_value(
        parsed.left,
        iteration_index=iteration_index,
        step_results=step_results,
    )
    expected = resolve_operand_value(
        parsed.right,
        iteration_index=iteration_index,
        step_results=step_results,
    )
    passed = _compare_values(actual, expected, parsed.operator)
    detail = _format_condition_detail(passed, actual)

    return ConditionEvaluationResult(
        expression=expression,
        passed=passed,
        actual=actual,
        expected=expected,
        detail=detail,
    )
