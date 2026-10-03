"""Condition expression parsing and authoring-time validation for loop 'until' clauses."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

_CONDITION_RE = re.compile(r"^\s*(?P<left>.+?)\s*(?P<op>==|!=|>=|<=|>|<|\bcontains\b)\s*(?P<right>.+?)\s*$")
_VALID_OPERATORS = frozenset({"==", "!=", ">=", "<=", ">", "<", "contains"})
_VALID_STEP_FIELDS = frozenset({"exit_code", "outputs", "status", "stdout"})


@dataclass(frozen=True)
class ParsedCondition:
    """Structured representation of a parsed comparison condition."""

    raw: str
    left: str
    operator: str
    right: str


def parse_condition_expression(expression: str) -> ParsedCondition | None:
    """Parse a condition expression string into a ParsedCondition, or None if malformed."""
    match = _CONDITION_RE.match(expression.strip())
    if not match:
        return None
    left = match.group("left").strip()
    op = match.group("op").strip()
    right = match.group("right").strip()
    if not left or not right or op not in _VALID_OPERATORS or right.startswith("="):
        return None
    return ParsedCondition(raw=expression, left=left, operator=op, right=right)


def parse_literal(raw: str) -> Any:
    """Parse a literal value from a condition operand string."""
    token = raw.strip()
    lower = token.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False

    try:
        val = ast.literal_eval(token)
        if isinstance(val, (int, float, str, bool)):
            return val
    except (ValueError, SyntaxError):
        pass

    return token


def _is_dynamic_operand(operand: str) -> bool:
    """Check whether an operand references dynamic step or iteration state."""
    return operand.startswith("steps.") or operand.startswith("iteration.") or operand in ("iteration",)


def _validate_step_operand(
    operand: str,
    expression: str,
    known_step_ids: set[str] | None,
) -> list[str]:
    """Validate step reference syntax and verify step ID against known IDs."""
    parts = operand.split(".")
    if len(parts) < 3:
        return [f"Invalid step reference '{operand}' in condition. Expected 'steps.<step_id>.<field>'."]

    step_id, field = parts[1], parts[2]
    errors: list[str] = []
    if known_step_ids is not None and step_id not in known_step_ids:
        allowed = ", ".join(sorted(known_step_ids)) or "none"
        errors.append(
            f"Step id '{step_id}' referenced in until condition '{expression}' "
            f"not found in loop 'do' steps (allowed: {allowed})."
        )
    if field not in _VALID_STEP_FIELDS:
        errors.append(
            f"Unknown step field '{field}' in '{operand}'. Allowed fields: {', '.join(sorted(_VALID_STEP_FIELDS))}."
        )
    return errors


def _validate_operand(
    operand: str,
    expression: str,
    known_step_ids: set[str] | None,
) -> list[str]:
    """Validate condition operand syntax and reference integrity."""
    if operand.startswith("steps."):
        return _validate_step_operand(operand, expression, known_step_ids)
    return []


def validate_condition_expression(
    expression: str,
    known_step_ids: set[str] | None = None,
) -> list[str]:
    """Validate syntax and step reference of an until condition expression.

    Returns a list of error message strings (empty if valid).
    """
    parsed = parse_condition_expression(expression)
    if parsed is None:
        return [
            f"Invalid condition expression '{expression}'. "
            f"Expected '<operand> <operator> <operand>' with operators: {', '.join(sorted(_VALID_OPERATORS))}."
        ]

    errors: list[str] = []
    errors.extend(_validate_operand(parsed.left, expression, known_step_ids))
    errors.extend(_validate_operand(parsed.right, expression, known_step_ids))

    if not _is_dynamic_operand(parsed.left) and not _is_dynamic_operand(parsed.right):
        errors.append(
            f"Condition '{expression}' must reference at least one dynamic operand ('steps.<id>.<field>' or 'iteration.index')."
        )

    return errors
