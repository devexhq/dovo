"""Contract tests for validate_loop_structure: persisted loop structure versus the frozen run snapshot."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock
from dovo.engine.models import DefinitionRef, DefinitionsManifest
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
)
from dovo.engine.state_validation import validate_loop_structure
from tests.harness.builders import BlueprintBuilder, StepBuilder

_MANIFEST = DefinitionsManifest(blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="a", resolved_at="now"))
_BODY_IDS = ["edit", "verify"]


def _blueprint() -> Blueprint:
    loop = LoopStepBlock(
        id="fix",
        type="loop",
        max_iterations=3,
        until=["steps.verify.exit_code == 0"],
        on_max_iterations=FailurePolicy.PROMPT_USER,
        do=[StepBuilder.command("true").with_id(step_id).build() for step_id in _BODY_IDS],
    )
    return Blueprint(BlueprintBuilder("bp").with_step(loop).build())


def _tree(loop_id: str = "fix", persisted_ids: list[str] | None = None) -> ExecutionStateTree:
    iteration = ExecutionIterationRecord(
        number=1, steps=[ExecutionLeafNode(id=step_id) for step_id in persisted_ids or _BODY_IDS]
    )
    loop = ExecutionLoopNode(id=loop_id, max_iterations=3, iterations=[iteration])
    return ExecutionStateTree(manifest=_MANIFEST, nodes=[loop])


class ValidateLoopStructureTests:
    """[tier-1/unit] validate_loop_structure: report every loop id or iteration body that differs from the snapshot."""

    def test_matching_tree_reports_no_errors(self) -> None:
        """[tier-1/unit] validate_loop_structure: a tree built by RunStateStore.initialize plus one iteration matching loop.do returns []."""
        assert validate_loop_structure(_tree(), _blueprint()) == []

    def test_loop_node_without_loop_definition_reports_id_mismatch(self) -> None:
        """[tier-1/unit] validate_loop_structure: a loop node id absent from the blueprint's loop definitions returns ["Loop 'ghost' does not match a loop in the run snapshot."]."""
        errors = validate_loop_structure(_tree(loop_id="ghost"), _blueprint())

        assert errors == ["Loop 'ghost' does not match a loop in the run snapshot."]

    @pytest.mark.parametrize(
        "persisted_ids",
        [
            pytest.param(["verify", "edit"], id="swapped"),
            pytest.param(["edit", "other"], id="renamed"),
            pytest.param(["edit"], id="short"),
            pytest.param(["edit", "verify", "extra"], id="long"),
        ],
    )
    def test_iteration_children_differing_from_body_report_step_mismatch(self, persisted_ids: list[str]) -> None:
        """[tier-1/unit] validate_loop_structure: iteration 1 leaf ids swapped, renamed, short, or long against do [edit, verify] return ["Loop 'fix' iteration 1 steps [...] do not match the loop body ['edit', 'verify']."]."""
        errors = validate_loop_structure(_tree(persisted_ids=persisted_ids), _blueprint())

        assert errors == [
            f"Loop 'fix' iteration 1 steps {persisted_ids} do not match the loop body ['edit', 'verify']."
        ]
