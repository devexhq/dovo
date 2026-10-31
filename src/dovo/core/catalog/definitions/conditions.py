"""Condition expression parsing and authoring-time validation shared by loop 'until' and step 'when' lists."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

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


def parse_literal(raw: str) -> bool | int | float | str:
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


@dataclass(frozen=True)
class ConditionScope:
    """Where a condition list sits: its clause name, the step ids it may reference, and whether 'iteration' resolves."""

    clause: str = "until"
    known_step_ids: frozenset[str] | None = None
    scope_label: str = "loop 'do' steps"
    allow_iteration: bool = True


def _validate_step_operand(operand: str, expression: str, scope: ConditionScope) -> list[str]:
    """Validate step reference syntax and verify step ID against known IDs."""
    parts = operand.split(".")
    if len(parts) < 3:
        return [f"Invalid step reference '{operand}' in condition. Expected 'steps.<step_id>.<field>'."]

    step_id, field = parts[1], parts[2]
    errors: list[str] = []
    known = scope.known_step_ids
    if known is not None and step_id not in known:
        allowed = ", ".join(sorted(known)) or "none"
        errors.append(
            f"Step id '{step_id}' referenced in {scope.clause} condition '{expression}' "
            f"not found in {scope.scope_label} (allowed: {allowed})."
        )
    if field not in _VALID_STEP_FIELDS:
        errors.append(
            f"Unknown step field '{field}' in '{operand}'. Allowed fields: {', '.join(sorted(_VALID_STEP_FIELDS))}."
        )
    return errors


def _validate_operand(operand: str, expression: str, scope: ConditionScope) -> list[str]:
    """Validate condition operand syntax and reference integrity."""
    if operand.startswith("steps."):
        return _validate_step_operand(operand, expression, scope)
    if not scope.allow_iteration and _is_dynamic_operand(operand):
        return [
            f"Operand '{operand}' in {scope.clause} condition '{expression}' is only available inside a loop's 'do'."
        ]
    return []


def validate_condition_expression(
    expression: str,
    known_step_ids: set[str] | None = None,
    *,
    scope: ConditionScope | None = None,
) -> list[str]:
    """Validate the syntax and operand references of one condition expression.

    known_step_ids alone checks a loop 'until' expression against its loop's 'do' step ids; scope, when given,
    names the clause and the step ids a 'when' or 'condition' expression may reference instead.

    Returns a list of error message strings (empty if valid).
    """
    if scope is None:
        scope = ConditionScope(known_step_ids=None if known_step_ids is None else frozenset(known_step_ids))
    parsed = parse_condition_expression(expression)
    if parsed is None:
        return [
            f"Invalid condition expression '{expression}'. "
            f"Expected '<operand> <operator> <operand>' with operators: {', '.join(sorted(_VALID_OPERATORS))}."
        ]

    errors: list[str] = []
    errors.extend(_validate_operand(parsed.left, expression, scope))
    errors.extend(_validate_operand(parsed.right, expression, scope))

    if not _is_dynamic_operand(parsed.left) and not _is_dynamic_operand(parsed.right):
        errors.append(
            f"Condition '{expression}' must reference at least one dynamic operand ('steps.<id>.<field>' or 'iteration.index')."
        )

    return errors
