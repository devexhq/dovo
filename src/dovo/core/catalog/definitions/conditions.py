"""Authoring-time validation of condition expressions, shared by loop 'until' and step 'when' lists."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from dovo.core.expressions import (
    Comparison,
    Expression,
    InvalidExpression,
    Logical,
    Negation,
    Operand,
    Path,
    ValueList,
    parse_expression,
)

_VALID_STEP_FIELDS = frozenset({"exit_code", "outputs", "status", "stdout"})
_STEPS_ROOT = "steps"
_ITERATION_ROOT = "iteration"


@dataclass(frozen=True)
class ConditionScope:
    """Where a condition list sits: its clause name, the step ids it may reference, and whether 'iteration' resolves."""

    clause: str = "until"
    known_step_ids: frozenset[str] | None = None
    scope_label: str = "loop 'do' steps"
    allow_iteration: bool = True


def _is_dynamic_operand(operand: Expression | ValueList) -> bool:
    """Return whether an operand reads step or iteration state; a lone 'steps' is a bare word."""
    if not isinstance(operand, Path):
        return False
    return operand.root == _ITERATION_ROOT or (operand.root == _STEPS_ROOT and bool(operand.segments))


def _is_unreadable_root(operand: Expression | ValueList) -> bool:
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


def _index_errors(operand: Path, expression: str, scope: ConditionScope) -> list[str]:
    """Reject an operand holding a bracket index: a condition reads a step by id, never by run-order position."""
    if not any(isinstance(segment, int) for segment in operand.segments):
        return []
    return [
        f"Operand '{operand.text}' in {scope.clause} condition '{expression}' holds a bracket index, "
        "which a condition cannot read."
    ]


def _validate_operand(operand: Expression | ValueList, expression: str, scope: ConditionScope) -> list[str]:
    """Validate one operand's reference: a bracket index, a step, the iteration, or a root a condition cannot read."""
    if not isinstance(operand, Path):
        return []
    if index_errors := _index_errors(operand, expression, scope):
        return index_errors
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


def _list_item_errors(item: Operand, expression: str, scope: ConditionScope) -> list[str]:
    """Reject a list item that reads a value: a dotted path, or one rooted at 'iteration'."""
    if not isinstance(item, Path) or not (item.segments or item.root == _ITERATION_ROOT):
        return []
    return [
        f"List item '{item.text}' in {scope.clause} condition '{expression}' is not a literal. "
        "Items after 'in' and 'not in' are quoted strings, numbers, booleans or single words."
    ]


def expression_leaves(node: Expression | ValueList) -> Iterator[tuple[Operand, bool]]:
    """Yield every operand of a tree, left to right, with whether it is an item of a list after 'in' or 'not in'."""
    if isinstance(node, Logical):
        yield from expression_leaves(node.left)
        yield from expression_leaves(node.right)
    elif isinstance(node, Negation):
        yield from expression_leaves(node.operand)
    elif isinstance(node, Comparison):
        yield from expression_leaves(node.left)
        yield from expression_leaves(node.right)
    elif isinstance(node, ValueList):
        yield from ((item, True) for item in node.items)
    else:
        yield node, False


def _tree_errors(expression: str, parsed: Expression, scope: ConditionScope) -> list[str]:
    """Return the operand and list-item errors of a parsed entry, and the dynamic-operand error unless one reads state."""
    errors: list[str] = []
    operands: list[Operand] = []
    for operand, is_item in expression_leaves(parsed):
        if is_item:
            errors.extend(_list_item_errors(operand, expression, scope))
            continue
        operands.append(operand)
        errors.extend(_validate_operand(operand, expression, scope))
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
    """Validate the syntax and operand references of one condition entry.

    known_step_ids alone checks a loop 'until' expression against its loop's 'do' step ids; scope, when given,
    names the clause and the step ids a 'when' or 'condition' expression may reference instead.

    Returns a list of error message strings (empty if valid). A syntax error returns its one error. Otherwise every
    operand of the entry, through '&&', '||', '!', parentheses and comparisons, is checked as a reference, every item
    of a list after 'in' or 'not in' must not read a value, and the entry as a whole needs one operand outside a list
    that reads step or iteration state.
    """
    if scope is None:
        scope = ConditionScope(known_step_ids=None if known_step_ids is None else frozenset(known_step_ids))
    parsed = parse_expression(expression)
    if isinstance(parsed, InvalidExpression):
        return [parsed.message]
    return _tree_errors(expression, parsed, scope)
