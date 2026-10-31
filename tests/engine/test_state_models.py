"""Contract tests for the execution-state tree models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from dovo.common.models import FailurePolicy
from dovo.core.interaction import InteractionRequest, RequestOption
from dovo.engine.executors.models import StepResult
from dovo.engine.models import DefinitionRef, DefinitionsManifest, SkipCause
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
    StepSkip,
)
from tests.harness.sessions import SEEDED_SPAN

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
            attempts=[StepAttemptRecord(number=1, span=SEEDED_SPAN, result=result)],
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

    def test_state_saved_before_skips_existed_loads_without_skip_records(self) -> None:
        saved = {
            "schema_version": 1,
            "revision": 3,
            "manifest": _manifest_dict(),
            "nodes": [
                {
                    "kind": "step",
                    "id": "setup",
                    "name": None,
                    "state": "completed",
                    "attempts": [],
                    "pending_ask": None,
                },
                {
                    "kind": "loop",
                    "id": "fix",
                    "max_iterations": 2,
                    "until": ["steps.edit.exit_code == 0"],
                    "on_max_iterations": "prompt_user",
                    "state": "pending",
                    "iterations": [{"number": 1, "state": "pending", "steps": [], "until_passed": None}],
                    "granted_iterations": 0,
                },
            ],
        }

        tree = ExecutionStateTree.model_validate_json(json.dumps(saved))

        setup, fix = tree.nodes
        assert isinstance(setup, ExecutionLeafNode) and isinstance(fix, ExecutionLoopNode)
        assert (setup.state, setup.skip, fix.state, fix.skip) == (NodeState.COMPLETED, None, NodeState.PENDING, None)

    def test_skipped_leaf_round_trips_with_its_skip_record(self) -> None:
        skip = StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.a.exit_code == 0")
        tree = ExecutionStateTree(
            manifest=_MANIFEST, nodes=[ExecutionLeafNode(id="b", state=NodeState.SKIPPED, skip=skip)]
        )

        assert ExecutionStateTree.model_validate_json(tree.model_dump_json()) == tree

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
