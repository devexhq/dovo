"""Contract tests for the execution-state tree models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worktree.common.models import FailurePolicy
from worktree.core.engine.models import DefinitionRef, DefinitionsManifest
from worktree.core.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from worktree.core.step.models import StepResult

_MANIFEST = DefinitionsManifest(
    blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="abc", resolved_at="2026-09-28T00:00:00+00:00")
)


def _manifest_dict() -> dict[str, object]:
    return _MANIFEST.model_dump()


class ExecutionStateTreeTests:
    """Contract tests for ExecutionStateTree validation and serialization."""

    def test_nested_loop_tree_round_trips_through_json(self) -> None:
        """[tier-1/unit] ExecutionStateTree: model_validate_json(model_dump_json()) of a tree with a loop node, one iteration, and a leaf attempt carrying a StepResult equals the original."""
        result = StepResult(step_id="edit", status="completed", exit_code=0, stdout="", stderr="", duration_seconds=0.5)
        leaf = ExecutionLeafNode(
            id="edit",
            state=NodeState.COMPLETED,
            attempts=[StepAttemptRecord(number=1, started_at="t0", completed_at="t1", result=result)],
        )
        loop = ExecutionLoopNode(
            id="fix",
            max_iterations=3,
            until=["edit.exit_code == 0"],
            on_max_iterations=FailurePolicy.ABORT,
            iterations=[ExecutionIterationRecord(number=1, steps=[leaf], until_passed=True)],
        )
        tree = ExecutionStateTree(revision=2, manifest=_MANIFEST, nodes=[ExecutionLeafNode(id="setup"), loop])

        restored = ExecutionStateTree.model_validate_json(tree.model_dump_json())

        assert restored == tree

    def test_unknown_node_kind_raises_validation_error(self) -> None:
        """[tier-1/unit] ExecutionStateTree: a node dict with kind 'parallel' raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            ExecutionStateTree.model_validate(
                {"manifest": _manifest_dict(), "nodes": [{"kind": "parallel", "id": "x"}]}
            )

    def test_extra_field_raises_validation_error(self) -> None:
        """[tier-1/unit] ExecutionStateTree: an unexpected top-level key raises pydantic.ValidationError (extra=forbid)."""
        with pytest.raises(ValidationError):
            ExecutionStateTree.model_validate({"manifest": _manifest_dict(), "unexpected": 1})


class ExecutionLoopNodeCeilingTests:
    """[tier-1/unit] ExecutionLoopNode: the iteration ceiling is max_iterations plus granted_iterations."""

    def test_iteration_ceiling_adds_granted_iterations_and_round_trips_through_json(self) -> None:
        """[tier-1/unit] ExecutionLoopNode: max_iterations 2 with granted_iterations 3 has iteration_ceiling 5, default granted_iterations is 0, and the tree round-trips through model_dump_json / model_validate_json unchanged."""
        granted = ExecutionLoopNode(id="l", max_iterations=2, granted_iterations=3)
        tree = ExecutionStateTree(manifest=_MANIFEST, nodes=[granted])

        assert granted.iteration_ceiling == 5
        assert ExecutionLoopNode(id="l", max_iterations=2).granted_iterations == 0
        assert ExecutionStateTree.model_validate_json(tree.model_dump_json()) == tree

    def test_negative_granted_iterations_raises_validation_error(self) -> None:
        """[tier-1/unit] ExecutionLoopNode: granted_iterations -1 raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            ExecutionLoopNode(id="l", max_iterations=2, granted_iterations=-1)
