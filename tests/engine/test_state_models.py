"""Contract tests for the execution-state tree models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.common.models import FailurePolicy
from dovo.core.interaction import InteractionRequest, RequestOption
from dovo.engine.executors.models import StepResult
from dovo.engine.models import DefinitionRef, DefinitionsManifest
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)

_MANIFEST = DefinitionsManifest(
    blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="abc", resolved_at="2026-09-28T00:00:00+00:00")
)


def _manifest_dict() -> dict[str, object]:
    return _MANIFEST.model_dump()


class ExecutionStateTreeTests:
    """Contract tests for ExecutionStateTree validation and serialization."""

    def test_nested_loop_tree_round_trips_through_json(self) -> None:
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
        with pytest.raises(ValidationError):
            ExecutionStateTree.model_validate(
                {"manifest": _manifest_dict(), "nodes": [{"kind": "parallel", "id": "x"}]}
            )

    def test_extra_field_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionStateTree.model_validate({"manifest": _manifest_dict(), "unexpected": 1})


class ExecutionLoopNodeCeilingTests:
    def test_iteration_ceiling_adds_granted_iterations_and_round_trips_through_json(self) -> None:
        granted = ExecutionLoopNode(id="l", max_iterations=2, granted_iterations=3)
        tree = ExecutionStateTree(manifest=_MANIFEST, nodes=[granted])

        assert granted.iteration_ceiling == 5
        assert ExecutionLoopNode(id="l", max_iterations=2).granted_iterations == 0
        assert ExecutionStateTree.model_validate_json(tree.model_dump_json()) == tree

    def test_negative_granted_iterations_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionLoopNode(id="l", max_iterations=2, granted_iterations=-1)


class PendingAskStateTests:
    """Contract tests for ExecutionLeafNode.pending_ask persistence."""

    def test_pending_ask_round_trips_and_defaults_to_none(self) -> None:
        request = InteractionRequest(
            step_id="approve",
            mode="select",
            message="Go?",
            options=[RequestOption(value="a", label="A"), RequestOption(value="b", label="B", fail=True)],
        )
        tree = ExecutionStateTree(
            manifest=_MANIFEST,
            nodes=[
                ExecutionLeafNode(id="approve", state=NodeState.PAUSED, pending_ask=request),
                ExecutionLeafNode(id="after"),
            ],
        )

        restored = ExecutionStateTree.model_validate_json(tree.model_dump_json())

        assert restored == tree
        leaf_without_key = ExecutionLeafNode.model_validate({"id": "plain"})
        assert leaf_without_key.pending_ask is None

    def test_invalid_pending_ask_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionLeafNode.model_validate(
                {"id": "approve", "pending_ask": {"step_id": "a", "mode": "dropdown", "message": "m"}}
            )
