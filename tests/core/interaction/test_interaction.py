"""Contract tests for the Interaction entrypoint."""

from __future__ import annotations

import pytest

from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import InteractionSpec
from dovo.core.interaction import (
    HeadlessAnswerInput,
    Interaction,
    InteractionAnswer,
    InteractionAnswerStatus,
    InteractionRequest,
    InteractionResolveStatus,
    RequestOption,
)
from tests.conftest import PublishArtifact


def _request() -> InteractionRequest:
    """Return a select request over approve / revise (comment required) / deny (fail)."""
    return InteractionRequest(
        step_id="approve",
        mode="select",
        message="Approve?",
        options=[
            RequestOption(value="approve", label="Approve"),
            RequestOption(value="revise", label="Revise", comment="required"),
            RequestOption(value="deny", label="Deny", fail=True),
        ],
    )


class InteractionEntrypointTests:
    """Contract tests for Interaction."""

    def test_resolve_reads_the_bound_store_and_applies_the_bound_redactor(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "old"})
        publish_artifact("plan", {"PLAN.md": "token s3cr3t-value"})
        interaction = Interaction(artifact_store, SecretRedactor([("API_TOKEN", "s3cr3t-value")]))
        spec = InteractionSpec.model_validate({"mode": "confirm", "message": "Go?", "artifact": "plan"})

        result = interaction.resolve("approve", spec, "Go?")

        assert result.request is not None
        assert result.request.artifact is not None
        assert result.request.artifact.revision == 2
        assert result.request.artifact.preview == ["token [REDACTED:API_TOKEN]"]

    def test_resolve_reports_a_missing_displayed_artifact(self, artifact_store: ArtifactStore) -> None:
        spec = InteractionSpec.model_validate({"mode": "confirm", "message": "Go?", "artifact": "missing"})

        result = Interaction(artifact_store, SecretRedactor([])).resolve("approve", spec, "Go?")

        assert (result.status, result.request) == (InteractionResolveStatus.ARTIFACT_NOT_FOUND, None)

    @pytest.mark.parametrize(
        ("selected", "expected"),
        [pytest.param(["revise"], "required", id="strict_option"), pytest.param(["approve"], "disabled", id="default")],
    )
    def test_comment_policy_follows_the_selected_options(self, selected: list[str], expected: str) -> None:
        assert Interaction.comment_policy(_request(), selected) == expected

    def test_validate_returns_the_normalized_answer_or_rejects_an_unknown_option(self) -> None:
        valid = Interaction.validate(_request(), InteractionAnswer(selected=["approve"]))
        invalid = Interaction.validate(_request(), InteractionAnswer(selected=["maybe"]))

        assert (valid.status, valid.answer) == (InteractionAnswerStatus.OK, InteractionAnswer(selected=["approve"]))
        assert (invalid.status, invalid.answer) == (InteractionAnswerStatus.INVALID, None)

    def test_parse_headless_validates_a_supplied_value_against_the_request(self) -> None:
        with_comment = Interaction.parse_headless(
            _request(), HeadlessAnswerInput(step_id="approve", value="revise", comment="why")
        )
        without = Interaction.parse_headless(_request(), HeadlessAnswerInput(step_id="approve", value="revise"))

        assert with_comment.ok
        assert not without.ok

    def test_parse_arguments_splits_answer_or_rejects_a_malformed_one(self) -> None:
        parsed = Interaction.parse_arguments("approve=revise", "why", None)
        malformed = Interaction.parse_arguments("approve", None, None)

        assert parsed.supplied == HeadlessAnswerInput(step_id="approve", value="revise", comment="why")
        assert (malformed.status, malformed.supplied) == (InteractionAnswerStatus.INVALID, None)

    def test_request_errors_reports_duplicate_values_only_for_a_broken_request(self) -> None:
        broken = _request().model_copy(update={"options": [RequestOption(value="a", label="A")] * 2})

        assert Interaction.request_errors(_request()) == []
        assert "option values are not unique" in Interaction.request_errors(broken)

    def test_outputs_reports_the_selection(self) -> None:
        chosen = Interaction.outputs(_request(), InteractionAnswer(selected=["approve"]))
        multiselect = _request().model_copy(update={"mode": "multiselect"})
        empty = Interaction.outputs(multiselect, InteractionAnswer())

        assert (chosen["selected"], chosen["selected_count"]) == ("approve", "1")
        assert empty["selected_count"] == "0"

    def test_failing_option_returns_the_chosen_fail_option_or_none(self) -> None:
        failing = Interaction.failing_option(_request(), InteractionAnswer(selected=["deny"]))

        assert failing is not None
        assert failing.value == "deny"
        assert Interaction.failing_option(_request(), InteractionAnswer(selected=["approve"])) is None
