"""Structural validation of a persisted execution-state tree against the frozen run snapshot."""

from __future__ import annotations

from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock
from dovo.engine.state_models import ExecutionLoopNode, ExecutionStateTree


def _iteration_errors(loop_node: ExecutionLoopNode, loop: LoopStepBlock) -> list[str]:
    """Return one message per iteration of loop_node whose leaf ids differ from loop.do in id or order."""
    expected = [step.id for step in loop.do]
    errors: list[str] = []
    for iteration in loop_node.iterations:
        actual = [leaf.id for leaf in iteration.steps]
        if actual != expected:
            errors.append(
                f"Loop '{loop_node.id}' iteration {iteration.number} steps {actual} "
                f"do not match the loop body {expected}."
            )
    return errors


def validate_loop_structure(state: ExecutionStateTree, blueprint: Blueprint) -> list[str]:
    """Return one message per loop node or iteration whose id, body step ids, or body order differs from the blueprint's loop definition."""
    loops = {step.id: step for step in blueprint.steps if isinstance(step, LoopStepBlock)}
    errors: list[str] = []
    for node in state.nodes:
        if node.kind != "loop":
            continue

        loop = loops.get(node.id)
        if loop is None:
            errors.append(f"Loop '{node.id}' does not match a loop in the run snapshot.")
            continue

        errors.extend(_iteration_errors(node, loop))
    return errors
