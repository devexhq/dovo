"""Condition expression parsing and authoring-time validation shared by loop 'until' and step 'when' lists."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from dovo.core.expressions import Comparison, InvalidExpression, Operand, Path, parse_expression

_CONDITION_RE = re.compile(r"^\s*(?P<left>.+?)\s*(?P<op>==|!=|>=|<=|>|<|\bcontains\b)\s*(?P<right>.+?)\s*$")
_VALID_OPERATORS = frozenset({"==", "!=", ">=", "<=", ">", "<", "contains"})
_VALID_STEP_FIELDS = frozenset({"exit_code", "outputs", "status", "stdout"})
# A quoted run, its escapes included; an unterminated one runs to the end of the source.
_QUOTED_RUN_RE = re.compile(r""""(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?""")
_OPERATOR_RE = re.compile(r"==|!=|<|>|(?<!\S)contains(?!\S)")
_STEPS_ROOT = "steps"
_ITERATION_ROOT = "iteration"


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


@dataclass(frozen=True)
class ConditionScope:
    """Where a condition list sits: its clause name, the step ids it may reference, and whether 'iteration' resolves."""

    clause: str = "until"
    known_step_ids: frozenset[str] | None = None
    scope_label: str = "loop 'do' steps"
    allow_iteration: bool = True


def _missing_operator(expression: str) -> bool:
    """Return whether a non-blank expression has no comparison operator outside its quoted runs."""
    unquoted = _QUOTED_RUN_RE.sub(" ", expression)
    return bool(expression.strip()) and _OPERATOR_RE.search(unquoted) is None


def _malformed_message(expression: str) -> str:
    """Return the message of an expression that is not '<operand> <operator> <operand>'."""
    return (
        f"Invalid condition expression '{expression}'. "
        f"Expected '<operand> <operator> <operand>' with operators: {', '.join(sorted(_VALID_OPERATORS))}."
    )


def _run_time_misreads(expression: str, comparison: Comparison) -> str | None:
    """Describe a form the run-time evaluator would misread: a bracket key, or an operator inside the left operand."""
    for operand in (comparison.left, comparison.right):
        if isinstance(operand, Path) and "[" in operand.text:
            return f"Invalid expression '{expression}': bracket key in '{operand.text}' is not supported in a condition yet."
    split = parse_condition_expression(expression)
    if split is None or split.operator != comparison.operator.value or split.left.strip() != comparison.left.text:
        return (
            f"Invalid expression '{expression}': the operand '{comparison.left.text}' holds an operator, "
            "which a condition does not support yet."
        )
    return None


def _is_dynamic_operand(operand: Operand) -> bool:
    """Return whether an operand reads step or iteration state; a lone 'steps' is a bare word."""
    if not isinstance(operand, Path):
        return False
    return operand.root == _ITERATION_ROOT or (operand.root == _STEPS_ROOT and bool(operand.segments))


def _is_unreadable_root(operand: Operand) -> bool:
    """Return whether an operand is a dotted path whose root a condition cannot read."""
    return isinstance(operand, Path) and bool(operand.segments) and operand.root not in (_STEPS_ROOT, _ITERATION_ROOT)


def _validate_step_operand(operand: Path, expression: str, scope: ConditionScope) -> list[str]:
    """Validate step reference syntax and verify step ID against known IDs."""
    if len(operand.segments) < 2:
        return [f"Invalid step reference '{operand.text}' in condition. Expected 'steps.<step_id>.<field>'."]

    step_id, field = operand.segments[0], operand.segments[1]
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
            f"Unknown step field '{field}' in '{operand.text}'. "
            f"Allowed fields: {', '.join(sorted(_VALID_STEP_FIELDS))}."
        )
    return errors


def _validate_operand(operand: Operand, expression: str, scope: ConditionScope) -> list[str]:
    """Validate one operand's reference: a step, the iteration, or a root a condition cannot read."""
    if not isinstance(operand, Path):
        return []
    if operand.root == _STEPS_ROOT and operand.segments:
        return _validate_step_operand(operand, expression, scope)
    if operand.root == _ITERATION_ROOT and not scope.allow_iteration:
        return [
            f"Operand '{operand.text}' in {scope.clause} condition '{expression}' is only available inside a loop's 'do'."
        ]
    if _is_unreadable_root(operand):
        roots = "iteration, steps" if scope.allow_iteration else "steps"
        return [
            f"Operand '{operand.text}' in {scope.clause} condition '{expression}' reads '{operand.root}', "
            f"which a condition cannot read. Readable roots: {roots}."
        ]
    return []


def _operand_errors(expression: str, comparison: Comparison, scope: ConditionScope) -> list[str]:
    """Return the operand errors of a comparison, and the dynamic-operand error unless a root is unreadable."""
    operands = (comparison.left, comparison.right)
    errors = [error for operand in operands for error in _validate_operand(operand, expression, scope)]
    if not any(_is_unreadable_root(operand) or _is_dynamic_operand(operand) for operand in operands):
        errors.append(
            f"Condition '{expression}' must reference at least one dynamic operand ('steps.<id>.<field>' or 'iteration.index')."
        )
    return errors


def validate_condition_expression(
    expression: str,
    known_step_ids: set[str] | None = None,
    *,
    scope: ConditionScope | None = None,
) -> list[str]:
    """Validate the syntax and operand references of one condition expression.

    known_step_ids alone checks a loop 'until' expression against its loop's 'do' step ids; scope, when given,
    names the clause and the step ids a 'when' or 'condition' expression may reference instead.

    Returns a list of error message strings (empty if valid). An expression with no operator outside quotes, a
    syntax error and a form the run-time evaluator would misread each return their one error.
    """
    if scope is None:
        scope = ConditionScope(known_step_ids=None if known_step_ids is None else frozenset(known_step_ids))
    if _missing_operator(expression):
        return [_malformed_message(expression)]
    parsed = parse_expression(expression)
    if isinstance(parsed, InvalidExpression):
        return [parsed.message]
    if not isinstance(parsed, Comparison):
        return [_malformed_message(expression)]
    misread = _run_time_misreads(expression, parsed)
    if misread is not None:
        return [misread]
    return _operand_errors(expression, parsed, scope)
