"""Contract tests for validate_block_structure: persisted loop and if-block structure versus the run snapshot."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import BlueprintDefinition, LoopStepBlock
from dovo.engine.models import DefinitionRef, DefinitionsManifest, IfBranch
from dovo.engine.state_models import (
    ExecutionIfNode,
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
)
from dovo.engine.state_validation import validate_block_structure
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
    def test_matching_tree_reports_no_errors(self) -> None:
        assert validate_block_structure(_tree(), _blueprint()) == []

    def test_loop_node_without_loop_definition_reports_id_mismatch(self) -> None:
        errors = validate_block_structure(_tree(loop_id="ghost"), _blueprint())

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
        errors = validate_block_structure(_tree(persisted_ids=persisted_ids), _blueprint())

        assert errors == [
            f"Loop 'fix' iteration 1 steps {persisted_ids} do not match the loop body ['edit', 'verify']."
        ]


def _routed_blueprint() -> Blueprint:
    block: dict[str, object] = {
        "id": "route",
        "type": "if",
        "condition": ["steps.check.exit_code == 0"],
        "then": [{"id": "ship", "run": "true"}],
        "else": [{"id": "fix", "run": "true"}],
    }
    return Blueprint(BlueprintDefinition.from_document({"steps": [{"id": "check", "run": "true"}, block]}, key="bp"))


def _if_tree(node: ExecutionIfNode) -> ExecutionStateTree:
    return ExecutionStateTree(manifest=_MANIFEST, nodes=[ExecutionLeafNode(id="check"), node])


class ValidateIfStructureTests:
    def test_started_block_matching_its_branch_reports_no_errors(self) -> None:
        node = ExecutionIfNode(id="route", branch=IfBranch.ELSE, steps=[ExecutionLeafNode(id="fix")])

        assert validate_block_structure(_if_tree(node), _routed_blueprint()) == []

    def test_branch_steps_differing_from_the_chosen_branch_report_a_mismatch(self) -> None:
        node = ExecutionIfNode(id="route", branch=IfBranch.ELSE, steps=[ExecutionLeafNode(id="ship")])

        errors = validate_block_structure(_if_tree(node), _routed_blueprint())

        assert errors == ["If block 'route' else steps ['ship'] do not match the branch ['fix']."]

    def test_if_node_without_an_if_block_reports_id_mismatch(self) -> None:
        errors = validate_block_structure(_if_tree(ExecutionIfNode(id="ghost")), _routed_blueprint())

        assert errors == ["If block 'ghost' does not match an if block in the run snapshot."]
