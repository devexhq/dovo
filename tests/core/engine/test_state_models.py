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
