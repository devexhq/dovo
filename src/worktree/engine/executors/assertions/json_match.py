"""JSON path/operator assertion evaluator for step stdout."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from worktree.engine.executors.assertions.assertion_helpers import short_pair, short_repr

_PATH_NOT_FOUND = object()


def evaluate_json_match(config: dict[str, Any], stdout: str) -> list[str]:
    """Parse stdout as JSON and check a dot-path value against an operator/value.

    Returns a single failure string on mismatch/error, or ``[]`` on success.
    Dot-paths walk nested mapping keys only (no list indexes).
    """
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return ["json_match: Invalid JSON output"]

    path = config["path"]
    actual = _resolve_json_path(parsed, path)
    if actual is _PATH_NOT_FOUND:
        return [f"json_match: JSON path '{path}' not found"]

    operator = config["operator"]
    comparator = _JSON_MATCH_OPERATORS.get(operator)
    if comparator is None:
        return [f"json_match: unsupported operator '{operator}'"]

    return comparator(actual, config["value"], path)


def _is_numeric(value: Any) -> bool:
    """Check whether a value is numeric (int or float, excluding bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _resolve_json_path(root: Any, path: str) -> Any:
    """Walk nested dict keys along ``path``. Return ``_PATH_NOT_FOUND`` if unresolved."""
    current = root
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return _PATH_NOT_FOUND
        current = current[segment]
    return current


def _compare_eq(actual: Any, value: Any, path: str) -> list[str]:
    """Assert that actual value equals expected value."""
    if actual == value:
        return []
    actual_repr, expected_repr = short_pair(actual, value)
    return [f"json_match: '{path}' was {actual_repr}, expected {expected_repr}"]


def _compare_neq(actual: Any, value: Any, path: str) -> list[str]:
    """Assert that actual value does not equal expected value."""
    if actual != value:
        return []
    actual_repr, expected_repr = short_pair(actual, value)
    return [f"json_match: '{path}' was {actual_repr}, expected not {expected_repr}"]


def _compare_contains(actual: Any, value: Any, path: str) -> list[str]:
    """Assert that actual collection contains expected value."""
    try:
        if value in actual:
            return []
    except TypeError:
        pass
    return [f"json_match: '{path}' does not contain {short_repr(value)} (was {short_repr(actual)})"]


def _compare_ordered(
    actual: Any,
    value: Any,
    path: str,
    operator: str,
    predicate: Callable[[Any, Any], bool],
    expected_phrase: str,
) -> list[str]:
    """Assert that numeric ordering comparison holds between actual and expected value."""
    if not _is_numeric(actual) or not _is_numeric(value):
        return [
            f"json_match: operator '{operator}' requires numeric values, "
            f"got {type(actual).__name__} and {type(value).__name__}"
        ]
    if predicate(actual, value):
        return []
    actual_repr, expected_repr = short_pair(actual, value)
    return [f"json_match: '{path}' was {actual_repr}, expected {expected_phrase} {expected_repr}"]


_JSON_MATCH_OPERATORS: dict[str, Callable[[Any, Any, str], list[str]]] = {
    "eq": _compare_eq,
    "neq": _compare_neq,
    "contains": _compare_contains,
    "gt": lambda actual, value, path: _compare_ordered(actual, value, path, "gt", lambda a, v: a > v, "greater than"),
    "gte": lambda actual, value, path: _compare_ordered(actual, value, path, "gte", lambda a, v: a >= v, "at least"),
    "lt": lambda actual, value, path: _compare_ordered(actual, value, path, "lt", lambda a, v: a < v, "less than"),
    "lte": lambda actual, value, path: _compare_ordered(actual, value, path, "lte", lambda a, v: a <= v, "at most"),
}
