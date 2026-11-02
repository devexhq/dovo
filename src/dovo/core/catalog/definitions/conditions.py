"""Authoring-time validation of condition expressions, shared by loop 'until' and step 'when' lists.

A condition reads three roots: 'steps', 'iteration' inside a loop's 'do', and 'artifacts'. An artifact operand must
name a field and an artifact some step of the blueprint publishes through its 'artifacts:'.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from dovo.core.expressions import (
    Call,
    Comparison,
    Expression,
    InvalidExpression,
    Logical,
    Negation,
    Operand,
    Path,
    Projection,
    ValueList,
    parse_expression,
)
from dovo.core.inputs import ARTIFACT_FIELDS, artifact_reference

_VALID_STEP_FIELDS = frozenset({"exit_code", "outputs", "status", "stdout"})
_STEPS_ROOT = "steps"
_ITERATION_ROOT = "iteration"
_ARTIFACTS_ROOT = "artifacts"
_READABLE_ROOTS = (_STEPS_ROOT, _ITERATION_ROOT, _ARTIFACTS_ROOT)


@dataclass(frozen=True)
class ConditionScope:
    """Where a condition list sits: its clause, the step ids it may reference, whether 'iteration' resolves, and the published artifacts."""

    clause: str = "until"
    known_step_ids: frozenset[str] | None = None
    scope_label: str = "loop 'do' steps"
    allow_iteration: bool = True
    published_artifacts: frozenset[str] = field(kw_only=True)


def _is_dynamic_operand(operand: Expression | ValueList) -> bool:
    """Return whether an operand reads step, iteration or artifact state; a lone 'steps' or 'artifacts' is a bare word."""
    if not isinstance(operand, Path):
        return False
    return operand.root == _ITERATION_ROOT or (
        operand.root in (_STEPS_ROOT, _ARTIFACTS_ROOT) and bool(operand.segments)
    )


def _is_unreadable_root(operand: Expression | ValueList) -> bool:
    """Return whether an operand is a dotted path whose root a condition cannot read."""
    return isinstance(operand, Path) and bool(operand.segments) and operand.root not in _READABLE_ROOTS


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


def _artifact_operand_errors(operand: Path, expression: str, scope: ConditionScope) -> list[str]:
    """Reject an artifact operand that names no field, or an artifact no step of the blueprint publishes."""
    reference = artifact_reference(operand.segments)
    if reference is None:
        return [
            f"Operand '{operand.text}' in {scope.clause} condition '{expression}' reads no artifact field. "
            f"Expected 'artifacts.<name>.<field>' with a field of {', '.join(sorted(ARTIFACT_FIELDS))}."
        ]
    if reference.name not in scope.published_artifacts:
        published = ", ".join(sorted(scope.published_artifacts)) or "none"
        return [
            f"Artifact '{reference.name}' read in {scope.clause} condition '{expression}' is not published by "
            f"any step's 'artifacts:' in the blueprint (published: {published})."
        ]
    return []


def _unreadable_root_errors(operand: Path, expression: str, scope: ConditionScope) -> list[str]:
    """Reject a dotted path whose root a condition cannot read, naming the roots it can."""
    if not _is_unreadable_root(operand):
        return []
    roots = "artifacts, iteration, steps" if scope.allow_iteration else "artifacts, steps"
    return [
        f"Operand '{operand.text}' in {scope.clause} condition '{expression}' reads '{operand.root}', "
        f"which a condition cannot read. Readable roots: {roots}."
    ]


def _validate_operand(operand: Expression | ValueList, expression: str, scope: ConditionScope) -> list[str]:
    """Validate one operand's reference: a bracket index, a step, an artifact, the iteration, or an unreadable root."""
    if not isinstance(operand, Path):
        return []
    if index_errors := _index_errors(operand, expression, scope):
        return index_errors
    if operand.root == _STEPS_ROOT and operand.segments:
        return _validate_step_operand(operand, expression, scope)
    if operand.root == _ARTIFACTS_ROOT and operand.segments:
        return _artifact_operand_errors(operand, expression, scope)
    if operand.root == _ITERATION_ROOT and not scope.allow_iteration:
        return [
            f"Operand '{operand.text}' in {scope.clause} condition '{expression}' is only available inside a loop's 'do'."
        ]
    return _unreadable_root_errors(operand, expression, scope)


def _list_item_errors(item: Operand | Projection, expression: str, scope: ConditionScope) -> list[str]:
    """Reject a list item that reads a value: a dotted path, or one rooted at 'iteration'."""
    if not isinstance(item, Path) or not (item.segments or item.root == _ITERATION_ROOT):
        return []
    return [
        f"List item '{item.text}' in {scope.clause} condition '{expression}' is not a literal. "
        "Items after 'in' and 'not in' are quoted strings, numbers, booleans or single words."
    ]


def expression_leaves(node: Expression | ValueList) -> Iterator[tuple[Operand | Projection, bool]]:
    """Yield every operand and projection of a tree, left to right, with whether it is an item of a list after 'in' or 'not in'.

    A projection is one leaf: its base is the operand it reads, and its keys are not operands.
    """
    if isinstance(node, Logical):
        yield from expression_leaves(node.left)
        yield from expression_leaves(node.right)
    elif isinstance(node, Negation):
        yield from expression_leaves(node.operand)
    elif isinstance(node, Comparison):
        yield from expression_leaves(node.left)
        yield from expression_leaves(node.right)
    elif isinstance(node, Call):
        for argument in node.arguments:
            yield from expression_leaves(argument)
    elif isinstance(node, ValueList):
        yield from ((item, True) for item in node.items)
    else:
        yield node, False


def _projection_errors(projection: Projection, expression: str, scope: ConditionScope) -> list[str]:
    """Reject a projection whose base is neither a step, iteration or artifact value nor a root a condition cannot read."""
    base = projection.base
    if _is_dynamic_operand(base) or _is_unreadable_root(base):
        return []
    return [
        f"Operand '{projection.text}' in {scope.clause} condition '{expression}' projects '{base.text}', "
        "which is not a step or iteration value."
    ]


def _tree_errors(expression: str, parsed: Expression, scope: ConditionScope) -> list[str]:
    """Return the operand and list-item errors of a parsed entry, and the dynamic-operand error unless one reads state.

    A projection's base is checked as an operand, and must read a step, iteration or artifact value.
    """
    errors: list[str] = []
    operands: list[Operand] = []
    for leaf, is_item in expression_leaves(parsed):
        if is_item:
            errors.extend(_list_item_errors(leaf, expression, scope))
            continue
        operand = leaf.base if isinstance(leaf, Projection) else leaf
        operands.append(operand)
        errors.extend(_validate_operand(operand, expression, scope))
        if isinstance(leaf, Projection):
            errors.extend(_projection_errors(leaf, expression, scope))
    if not any(_is_unreadable_root(operand) or _is_dynamic_operand(operand) for operand in operands):
        errors.append(
            f"Condition '{expression}' must reference at least one dynamic operand ('steps.<id>.<field>' or 'iteration.index')."
        )
    return errors


def validate_condition_expression(expression: str, *, scope: ConditionScope) -> list[str]:
    """Validate the syntax and operand references of one condition entry against scope.

    scope names the clause, the step ids the entry may reference, whether 'iteration' resolves, and the artifact
    names the blueprint publishes.

    Returns a list of error message strings (empty if valid). A syntax error returns its one error. Otherwise every
    operand of the entry, through '&&', '||', '!', parentheses, comparisons and call arguments, is checked as a
    reference, every item of a list after 'in' or 'not in' must not read a value, and the entry as a whole needs one
    operand outside a list that reads step, iteration or artifact state.
    """
    parsed = parse_expression(expression)
    if isinstance(parsed, InvalidExpression):
        return [parsed.message]
    return _tree_errors(expression, parsed, scope)
