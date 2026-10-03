"""Projections of the execution state tree for RunOutcome and step metadata consumers."""

from __future__ import annotations

from collections.abc import Iterator

from dovo.core.db import RunRecord
from dovo.engine.executors.metadata import previous_step_metadata_from_result
from dovo.engine.executors.models import PreviousStepMetadata, StepResult
from dovo.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionStateTree,
    NodeState,
    RunJsonPayload,
    RunLifecycle,
)

_TERMINAL_LEAF_STATES = TERMINAL_NODE_STATES - {NodeState.CANCELLED}


def iter_leaves(state: ExecutionStateTree) -> Iterator[ExecutionLeafNode]:
    """Yield every leaf in tree order, descending through loop iterations."""
    for node in state.nodes:
        if node.kind == "step":
            yield node
            continue
        for iteration in node.iterations:
            yield from iteration.steps


def terminal_result(leaf: ExecutionLeafNode) -> StepResult | None:
    """Return the last attempt's result when the leaf is terminal and has one, else None."""
    if leaf.state not in _TERMINAL_LEAF_STATES or not leaf.attempts:
        return None
    return leaf.attempts[-1].result


def iteration_results(iteration: ExecutionIterationRecord) -> dict[str, StepResult]:
    """Map step id to terminal result for every terminal body leaf of iteration that has one, in body order."""
    results: dict[str, StepResult] = {}
    for leaf in iteration.steps:
        result = terminal_result(leaf)
        if result is not None:
            results[leaf.id] = result
    return results


def _terminal_leaves(state: ExecutionStateTree) -> Iterator[tuple[ExecutionLeafNode, StepResult]]:
    """Yield each terminal leaf with its last attempt's result, in tree order through loop iterations."""
    for leaf in iter_leaves(state):
        result = terminal_result(leaf)
        if result is not None:
            yield leaf, result


def flatten_step_results(state: ExecutionStateTree) -> list[StepResult]:
    """Flatten terminal leaf results from linear steps and loop iterations into an ordered list for RunOutcome and CLI consumers."""
    return [result for _, result in _terminal_leaves(state)]


def terminal_step_metadata(state: ExecutionStateTree) -> list[PreviousStepMetadata]:
    """Build PreviousStepMetadata (1-based index, leaf name) for every terminal leaf in tree order."""
    return [
        previous_step_metadata_from_result(result, step_index=position, step_name=leaf.name or "")
        for position, (leaf, result) in enumerate(_terminal_leaves(state), start=1)
    ]


def build_run_json_payload(state: ExecutionStateTree, row: RunRecord) -> RunJsonPayload:
    """Project the execution tree and its run row into the run.json payload, with results flattened from the tree."""
    return RunJsonPayload(
        revision=state.revision,
        manifest=state.manifest,
        nodes=state.nodes,
        lifecycle=RunLifecycle(
            status=row.status,
            error_message=row.error_message,
            started_at=row.started_at,
            completed_at=row.completed_at,
            sandbox_id=row.sandbox_id,
            sandbox_kept=row.sandbox_kept,
        ),
        results=flatten_step_results(state),
    )
