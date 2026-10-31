"""Authoring-time checks of 'when' and 'condition' lists: each expression may reference only earlier steps of its own or an enclosing scope."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.core.catalog.definitions.conditions import ConditionScope, validate_condition_expression
from dovo.core.catalog.definitions.step import BlueprintStep, IfStepBlock, LoopStepBlock, StepDefinition

_EARLIER_STEPS = "earlier steps"


def _owner_errors(owner: str, expressions: list[str] | None, scope: ConditionScope) -> list[str]:
    """Return every error of one condition list, each prefixed with the step or block that owns it."""
    return [
        f"{owner}: {error}"
        for expression in expressions or []
        for error in validate_condition_expression(expression, scope=scope)
    ]


def _when_scope(known: frozenset[str], *, in_loop: bool) -> ConditionScope:
    """Return the scope of a 'when' list that may reference the known step ids, and 'iteration' only inside a loop."""
    return ConditionScope(clause="when", known_step_ids=known, scope_label=_EARLIER_STEPS, allow_iteration=in_loop)


def _sequence_errors(steps: Sequence[StepDefinition], outer: frozenset[str], *, in_loop: bool) -> list[str]:
    """Return the 'when' errors of a step list whose steps may reference outer ids and the steps before them."""
    errors: list[str] = []
    seen: set[str] = set()
    for step in steps:
        errors.extend(_owner_errors(f"Step '{step.id}'", step.when, _when_scope(outer | seen, in_loop=in_loop)))
        seen.add(step.id)
    return errors


def _loop_errors(loop: LoopStepBlock, outer: frozenset[str]) -> list[str]:
    """Return the errors of a loop's own 'when' and of each 'do' step's 'when'."""
    return [
        *_owner_errors(f"Loop '{loop.id}'", loop.when, _when_scope(outer, in_loop=False)),
        *_sequence_errors(loop.do, outer, in_loop=True),
    ]


def _if_errors(block: IfStepBlock, outer: frozenset[str]) -> list[str]:
    """Return the errors of an if block's 'condition' and of each branch step's 'when'."""
    scope = ConditionScope(clause="if", known_step_ids=outer, scope_label=_EARLIER_STEPS, allow_iteration=False)
    return [
        *_owner_errors(f"If block '{block.id}'", block.condition, scope),
        *_sequence_errors(block.branch("then"), outer, in_loop=False),
        *_sequence_errors(block.branch("else"), outer, in_loop=False),
    ]


def condition_reference_errors(steps: Sequence[BlueprintStep]) -> list[str]:
    """Return one message per invalid 'when' or 'condition' expression in a blueprint's steps, naming its owner.

    A top-level expression may reference earlier top-level steps and the steps of either branch of an earlier if
    block. A 'do' step's expression may also reference earlier steps of the same 'do', and 'iteration'. An if
    block's condition sees the steps before the block, and a branch step also the earlier steps of its branch.
    A loop's or if block's own id is never visible, and neither are a loop's 'do' steps after the loop.
    """
    errors: list[str] = []
    known: set[str] = set()
    for step in steps:
        outer = frozenset(known)
        if isinstance(step, LoopStepBlock):
            errors.extend(_loop_errors(step, outer))
        elif isinstance(step, IfStepBlock):
            errors.extend(_if_errors(step, outer))
            known.update(branch_step.id for branch_step in [*step.branch("then"), *step.branch("else")])
        else:
            errors.extend(_owner_errors(f"Step '{step.id}'", step.when, _when_scope(outer, in_loop=False)))
            known.add(step.id)
    return errors
