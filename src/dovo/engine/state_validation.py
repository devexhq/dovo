"""Structural validation of a persisted execution-state tree against the frozen run snapshot."""

from __future__ import annotations

from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import IfStepBlock, LoopStepBlock
from dovo.engine.state_models import ExecutionIfNode, ExecutionLoopNode, ExecutionStateTree


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


def _branch_errors(if_node: ExecutionIfNode, block: IfStepBlock) -> list[str]:
    """Return a message when a started if block's leaf ids differ from its chosen branch in id or order."""
    if if_node.branch is None:
        return []
    expected = [step.id for step in block.branch(if_node.branch.value)]
    actual = [leaf.id for leaf in if_node.steps]
    if actual == expected:
        return []
    return [f"If block '{if_node.id}' {if_node.branch.value} steps {actual} do not match the branch {expected}."]


def validate_block_structure(state: ExecutionStateTree, blueprint: Blueprint) -> list[str]:
    """Return one message per loop or if node whose id, body or branch step ids, or their order differ from the blueprint."""
    blocks = {step.id: step for step in blueprint.steps if isinstance(step, (LoopStepBlock, IfStepBlock))}
    errors: list[str] = []
    for node in state.nodes:
        if isinstance(node, ExecutionLoopNode | ExecutionIfNode):
            errors.extend(_block_errors(node, blocks.get(node.id)))
    return errors


def _block_errors(node: ExecutionLoopNode | ExecutionIfNode, block: LoopStepBlock | IfStepBlock | None) -> list[str]:
    """Return the structure errors of one loop or if node against the blueprint block with its id, if any."""
    if isinstance(node, ExecutionLoopNode):
        if isinstance(block, LoopStepBlock):
            return _iteration_errors(node, block)
        return [f"Loop '{node.id}' does not match a loop in the run snapshot."]
    if isinstance(block, IfStepBlock):
        return _branch_errors(node, block)
    return [f"If block '{node.id}' does not match an if block in the run snapshot."]
