"""Contract tests for execution-state projections consumed by RunOutcome and step metadata."""

from __future__ import annotations

from worktree.core.engine.models import DefinitionRef, DefinitionsManifest
from worktree.core.engine.projection import (
    flatten_step_results,
    iter_leaves,
    iteration_results,
    terminal_step_metadata,
)
from worktree.core.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from worktree.core.step.models import StepResult

_MANIFEST = DefinitionsManifest(blueprint=DefinitionRef(ref="repo:blueprint:demo", sha="sha", resolved_at="now"))


def _result(step_id: str, status: str = "completed", attempts: int = 1) -> StepResult:
    return StepResult(
        step_id=step_id,
        status=status,
        exit_code=0 if status != "failed" else 1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        attempts=attempts,
    )


def _leaf(
    step_id: str,
    state: NodeState,
    results: list[StepResult | None] | None = None,
    name: str | None = None,
) -> ExecutionLeafNode:
    attempts = [
        StepAttemptRecord(number=number, started_at="t", completed_at="t", result=result)
        for number, result in enumerate(results or [], start=1)
    ]
    return ExecutionLeafNode(id=step_id, name=name, state=state, attempts=attempts)


def _tree(*nodes: ExecutionLeafNode | ExecutionLoopNode) -> ExecutionStateTree:
    return ExecutionStateTree(manifest=_MANIFEST, nodes=list(nodes))


class ProjectionTests:
    """[tier-1/unit] flatten_step_results and terminal_step_metadata: ordered projections of terminal leaves."""

    def test_flatten_returns_terminal_leaf_results_in_tree_order_including_loop_iterations(self) -> None:
        """[tier-1/unit] flatten_step_results: a tree of completed leaf a, a loop with two iterations of two leaves, and ignored leaf z returns those results in that exact order."""
        loop = ExecutionLoopNode(
            id="loop",
            max_iterations=2,
            state=NodeState.COMPLETED,
            iterations=[
                ExecutionIterationRecord(
                    number=turn,
                    state=NodeState.COMPLETED,
                    steps=[
                        _leaf("s1", NodeState.COMPLETED, [_result("s1", attempts=turn)]),
                        _leaf("s2", NodeState.COMPLETED, [_result("s2", attempts=turn)]),
                    ],
                )
                for turn in (1, 2)
            ],
        )
        tree = _tree(
            _leaf("a", NodeState.COMPLETED, [_result("a")]),
            loop,
            _leaf("z", NodeState.IGNORED, [_result("z", "ignored")]),
        )

        flattened = flatten_step_results(tree)

        assert [(result.step_id, result.attempts) for result in flattened] == [
            ("a", 1),
            ("s1", 1),
            ("s2", 1),
            ("s1", 2),
            ("s2", 2),
            ("z", 1),
        ]

    def test_flatten_excludes_pending_running_paused_and_cancelled_leaves(self) -> None:
        """[tier-1/unit] flatten_step_results: leaves in PENDING, RUNNING, PAUSED, and CANCELLED states contribute no result; a FAILED leaf contributes its last attempt's result."""
        failed_first = _result("f", "failed", attempts=1)
        failed_last = _result("f", "failed", attempts=2)
        tree = _tree(
            _leaf("pending", NodeState.PENDING),
            _leaf("running", NodeState.RUNNING, [None]),
            _leaf("paused", NodeState.PAUSED, [_result("paused", "failed")]),
            _leaf("cancelled", NodeState.CANCELLED, [_result("cancelled")]),
            _leaf("f", NodeState.FAILED, [failed_first, failed_last]),
        )

        assert flatten_step_results(tree) == [failed_last]

    def test_terminal_step_metadata_indexes_from_one_with_leaf_names(self) -> None:
        """[tier-1/unit] terminal_step_metadata: two terminal leaves named "Build" and None return PreviousStepMetadata with index "1"/"2" and name "Build"/"" and their statuses."""
        tree = _tree(
            _leaf("build", NodeState.COMPLETED, [_result("build")], name="Build"),
            _leaf("lint", NodeState.FAILED, [_result("lint", "failed")]),
        )

        metadata = terminal_step_metadata(tree)

        assert [(item.id, item.index, item.name, item.status) for item in metadata] == [
            ("build", "1", "Build", "completed"),
            ("lint", "2", "", "failed"),
        ]

    def test_terminal_step_metadata_returns_empty_list_when_no_leaf_is_terminal(self) -> None:
        """[tier-1/unit] terminal_step_metadata: a tree whose leaves are all PENDING returns []."""
        tree = _tree(_leaf("a", NodeState.PENDING), _leaf("b", NodeState.PENDING))

        assert terminal_step_metadata(tree) == []


class ProjectionIterLeavesTests:
    """[tier-1/unit] iter_leaves: every leaf in tree order, descending through loop iterations."""

    def test_iter_leaves_yields_top_level_and_loop_body_leaves_in_tree_order(self) -> None:
        """[tier-1/unit] iter_leaves: tree [a, loop(iter1=[s1, s2], iter2=[s1, s2]), z] yields ids [a, s1, s2, s1, s2, z]."""
        loop = ExecutionLoopNode(
            id="loop",
            max_iterations=2,
            iterations=[
                ExecutionIterationRecord(
                    number=turn, steps=[_leaf("s1", NodeState.PENDING), _leaf("s2", NodeState.PENDING)]
                )
                for turn in (1, 2)
            ],
        )
        tree = _tree(_leaf("a", NodeState.PENDING), loop, _leaf("z", NodeState.PENDING))

        assert [leaf.id for leaf in iter_leaves(tree)] == ["a", "s1", "s2", "s1", "s2", "z"]


class IterationResultsTests:
    """[tier-1/unit] iteration_results: terminal body results of one iteration keyed by step id."""

    def test_only_terminal_leaves_with_results_are_mapped_in_body_order(self) -> None:
        """[tier-1/unit] iteration_results: leaves [completed a, ignored b, pending c, running-without-result d] return {a: result_a, b: result_b} in that order."""
        result_a = _result("a")
        result_b = _result("b", "ignored")
        iteration = ExecutionIterationRecord(
            number=1,
            steps=[
                _leaf("a", NodeState.COMPLETED, [result_a]),
                _leaf("b", NodeState.IGNORED, [result_b]),
                _leaf("c", NodeState.PENDING),
                _leaf("d", NodeState.RUNNING, [None]),
            ],
        )

        results = iteration_results(iteration)

        assert list(results.items()) == [("a", result_a), ("b", result_b)]
