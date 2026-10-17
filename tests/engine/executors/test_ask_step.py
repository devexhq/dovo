"""Contract tests for the ask step executor: request resolution and answer completion."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.interaction import InteractionAnswer
from dovo.engine.executors import evaluate_condition
from dovo.engine.executors.ask_step import complete_ask_step, resolve_ask_request
from dovo.engine.executors.models import PreviousStepMetadata, StepExecutionContext
from tests.conftest import PublishArtifact

_MESSAGE = "Approve ${{ inputs.feature }} ({{ steps.plan.outputs.title }}, {{ artifacts.plan.revision }})?"
_OPTIONS_FROM = {
    "artifact": "review",
    "file": "review.json",
    "items_path": "findings",
    "value_field": "id",
    "label_field": "summary",
}


def _context(
    paths: WorkspacePaths, store: ArtifactStore, interaction: Mapping[str, object], step_id: str = "approve"
) -> StepExecutionContext:
    """Build an ask-step execution context for session wf_1 with feature input and a prior `plan` step."""
    step = StepDefinition.model_validate({"id": step_id, "type": "ask", "interaction": interaction})
    return StepExecutionContext(
        step=step,
        worktree_path=paths.root_dir,
        context={"inputs": {"feature": "export"}},
        previous_step=PreviousStepMetadata(id="plan", outputs={"title": "Export plan"}),
        steps=[PreviousStepMetadata(id="plan", outputs={"title": "Export plan"})],
        session_id="wf_1",
        artifact_store=store,
        paths=paths,
        sensitive_variables=("ASK_TEST_SECRET",),
    )


@pytest.fixture
def ask_context(engine_paths: WorkspacePaths, artifact_store: ArtifactStore) -> StepExecutionContext:
    """Context for a select ask step `approve` over approve / revise / deny."""
    interaction: dict[str, object] = {
        "mode": "select",
        "message": "Approve?",
        "options": [
            {"value": "approve", "label": "Approve"},
            {"value": "revise", "label": "Revise", "comment": "required"},
            {"value": "deny", "label": "Deny", "fail": True},
        ],
        "comment": {"policy": "optional"},
    }
    return _context(engine_paths, artifact_store, interaction)


class AskStepExecutorTests:
    """Contract tests for resolve_ask_request and complete_ask_step."""

    def test_message_interpolates_inputs_steps_and_artifacts(
        self, engine_paths: WorkspacePaths, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        context = _context(engine_paths, artifact_store, {"mode": "confirm", "message": _MESSAGE})
        assert context.artifact_store is not None
        publish_artifact("plan", {"PLAN.md": "plan"})

        resolution = resolve_ask_request(context)

        assert resolution.request is not None
        assert resolution.request.message == "Approve export (Export plan, 1)?"

    def test_failed_resolution_becomes_failed_step_result_with_code(
        self, engine_paths: WorkspacePaths, artifact_store: ArtifactStore
    ) -> None:
        interaction = {"mode": "multiselect", "message": "Fix?", "options_from": _OPTIONS_FROM}

        resolution = resolve_ask_request(_context(engine_paths, artifact_store, interaction, "triage"))

        assert resolution.request is None
        assert resolution.result is not None
        assert (resolution.result.status, resolution.result.exit_code) == ("failed", 1)
        assert "(ASK_OPTIONS_ARTIFACT_NOT_FOUND)" in (resolution.result.error_message or "")

    def test_empty_multiselect_settles_completed_without_a_request(
        self, engine_paths: WorkspacePaths, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        interaction = {"mode": "multiselect", "message": "Fix?", "options_from": _OPTIONS_FROM}
        context = _context(engine_paths, artifact_store, interaction, "triage")
        assert context.artifact_store is not None
        publish_artifact("review", {"review.json": json.dumps({"findings": []})})

        resolution = resolve_ask_request(context)

        assert resolution.request is None
        assert resolution.result is not None
        assert resolution.result.status == "completed"
        assert (resolution.result.outputs["selected"], resolution.result.outputs["selected_count"]) == ("[]", "0")
        assert resolution.result.warnings == [
            "No options to choose from for 'triage'; continuing with an empty selection."
        ]

    def test_answered_step_completes_with_masked_outputs_and_json_stdout(
        self, ask_context: StepExecutionContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASK_TEST_SECRET", "s3cr3t-value")
        request = resolve_ask_request(ask_context).request
        assert request is not None

        result = complete_ask_step(
            ask_context, request, InteractionAnswer(selected=["revise"], comment="use s3cr3t-value")
        )

        assert (result.status, result.exit_code) == ("completed", 0)
        assert result.outputs["comment"] == "use [REDACTED:ASK_TEST_SECRET]"
        assert json.loads(result.stdout) == {
            "selected": "revise",
            "selected_count": 1,
            "comment": "use [REDACTED:ASK_TEST_SECRET]",
            "text": "",
            "artifact_revision": "",
            "artifact_digest": "",
        }

    def test_fail_option_returns_failed_result_with_recorded_outputs(self, ask_context: StepExecutionContext) -> None:
        request = resolve_ask_request(ask_context).request
        assert request is not None

        result = complete_ask_step(ask_context, request, InteractionAnswer(selected=["deny"]))

        assert (result.status, result.exit_code) == ("failed", 1)
        assert result.error_message == "Prompt 'approve' answered 'deny'."
        assert result.outputs["selected"] == "deny"

    @pytest.mark.parametrize(
        ("expression", "selected", "expected"),
        [
            pytest.param('steps.approve.outputs.selected == "approve"', ["approve"], True, id="approved"),
            pytest.param('steps.approve.outputs.selected == "approve"', ["revise"], False, id="revised"),
            pytest.param("steps.approve.outputs.selected_count == 1", ["approve"], True, id="count_one"),
        ],
    )
    def test_stdout_json_drives_until_conditions(
        self, ask_context: StepExecutionContext, expression: str, selected: list[str], expected: bool
    ) -> None:
        request = resolve_ask_request(ask_context).request
        assert request is not None
        result = complete_ask_step(ask_context, request, InteractionAnswer(selected=selected, comment="c"))

        evaluation = evaluate_condition(expression, step_results={"approve": result})

        assert evaluation.passed is expected

    def test_empty_selection_stdout_satisfies_zero_count_condition(
        self, engine_paths: WorkspacePaths, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        interaction = {"mode": "multiselect", "message": "Fix?", "options_from": _OPTIONS_FROM}
        context = _context(engine_paths, artifact_store, interaction, "triage")
        assert context.artifact_store is not None
        publish_artifact("review", {"review.json": json.dumps({"findings": []})})
        settled = resolve_ask_request(context).result
        assert settled is not None

        evaluation = evaluate_condition("steps.triage.outputs.selected_count == 0", step_results={"triage": settled})

        assert evaluation.passed is True
