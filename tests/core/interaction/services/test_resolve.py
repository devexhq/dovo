"""Contract tests for resolving interaction blocks into requests."""

from __future__ import annotations

import json

import pytest

from dovo.common.redact import SecretRedactor
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import InteractionSpec
from dovo.core.interaction import InteractionResolveStatus
from dovo.core.interaction.services.resolve import resolve_request
from tests.conftest import PublishArtifact

_NO_SECRETS = SecretRedactor([])
_FINDINGS_FROM = {
    "artifact": "review",
    "file": "review.json",
    "items_path": "findings",
    "value_field": "id",
    "label_field": "summary",
}


def _spec(**fields: object) -> InteractionSpec:
    """Build an InteractionSpec from fields, defaulting to a confirm prompt."""
    return InteractionSpec.model_validate({"mode": "confirm", "message": "Go?", **fields})


def _findings(items: object) -> dict[str, str]:
    """Return the files of a review artifact holding items under `findings`."""
    return {"review.json": json.dumps({"findings": items})}


class ResolveRequestTests:
    """Contract tests for resolve_request."""

    def test_confirm_without_options_defaults_to_approve_and_failing_deny(self) -> None:
        spec = _spec(comment={"policy": "optional"})

        result = resolve_request("approve", spec, "Go?", None, _NO_SECRETS)

        assert result.request is not None
        assert [(o.value, o.label, o.fail, o.comment) for o in result.request.options] == [
            ("approve", "Approve", False, "optional"),
            ("deny", "Deny", True, "optional"),
        ]

    def test_option_comment_override_beats_interaction_default(self) -> None:
        spec = _spec(
            mode="select",
            comment={"policy": "optional"},
            options=[
                {"value": "a", "label": "A", "comment": "required"},
                {"value": "b", "label": "B"},
            ],
        )

        result = resolve_request("pick", spec, "Pick", None, _NO_SECRETS)

        assert result.request is not None
        assert [o.comment for o in result.request.options] == ["required", "optional"]

    def test_text_mode_resolves_without_options(self) -> None:
        result = resolve_request("ask", _spec(mode="text"), "Why?", None, _NO_SECRETS)

        assert result.status == InteractionResolveStatus.OK
        assert result.request is not None
        assert result.request.options == []

    def test_options_from_converts_values_with_str_and_keeps_item_order(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("review", _findings([{"id": 7, "summary": "a"}, {"id": "B2", "summary": "b"}]))
        spec = _spec(mode="multiselect", options_from=_FINDINGS_FROM)

        result = resolve_request("triage", spec, "Fix?", artifact_store, _NO_SECRETS)

        assert result.request is not None
        assert [(o.value, o.label) for o in result.request.options] == [("7", "a"), ("B2", "b")]

    def test_display_artifact_uses_latest_revision_and_records_it(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "v1"})
        publish_artifact("plan", {"PLAN.md": "v2"})

        result = resolve_request("approve", _spec(artifact="plan"), "Go?", artifact_store, _NO_SECRETS)

        assert result.request is not None
        assert result.request.artifact is not None
        assert result.request.artifact.revision == 2
        assert result.request.artifact.preview == ["v2"]
        assert result.request.artifact.digest != ""

    def test_multiselect_empty_array_returns_no_options_request(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("review", _findings([]))
        spec = _spec(mode="multiselect", options_from=_FINDINGS_FROM)

        result = resolve_request("triage", spec, "Fix?", artifact_store, _NO_SECRETS)

        assert result.status == InteractionResolveStatus.NO_OPTIONS
        assert result.ok is True
        assert result.request is not None
        assert result.request.options == []

    @pytest.mark.parametrize(
        ("files", "options_from", "status", "code"),
        [
            pytest.param(
                None,
                _FINDINGS_FROM,
                InteractionResolveStatus.OPTIONS_ARTIFACT_NOT_FOUND,
                "ASK_OPTIONS_ARTIFACT_NOT_FOUND",
                id="missing_artifact",
            ),
            pytest.param(
                _findings([]),
                {**_FINDINGS_FROM, "file": "other.json"},
                InteractionResolveStatus.OPTIONS_INVALID,
                "ASK_OPTIONS_INVALID",
                id="file_not_in_artifact",
            ),
            pytest.param(
                {"review.json": "{not json"},
                _FINDINGS_FROM,
                InteractionResolveStatus.OPTIONS_INVALID,
                "ASK_OPTIONS_INVALID",
                id="invalid_json",
            ),
            pytest.param(
                {"review.json": json.dumps({"findings": {"id": 1}})},
                _FINDINGS_FROM,
                InteractionResolveStatus.OPTIONS_INVALID,
                "ASK_OPTIONS_INVALID",
                id="path_not_array",
            ),
            pytest.param(
                _findings([{"id": "B1"}]),
                _FINDINGS_FROM,
                InteractionResolveStatus.OPTIONS_INVALID,
                "ASK_OPTIONS_INVALID",
                id="missing_label_field",
            ),
            pytest.param(
                _findings([{"id": "bad value", "summary": "s"}]),
                _FINDINGS_FROM,
                InteractionResolveStatus.OPTIONS_INVALID,
                "ASK_OPTIONS_INVALID",
                id="value_breaks_pattern",
            ),
        ],
    )
    def test_options_failures_report_their_status_and_code(
        self,
        artifact_store: ArtifactStore,
        publish_artifact: PublishArtifact,
        files: dict[str, str] | None,
        options_from: dict[str, str],
        status: InteractionResolveStatus,
        code: str,
    ) -> None:
        if files is not None:
            publish_artifact("review", files)
        spec = _spec(mode="multiselect", options_from=options_from)

        result = resolve_request("triage", spec, "Fix?", artifact_store, _NO_SECRETS)

        assert result.status == status
        assert result.error_code == code
        assert len(result.errors) == 1
        assert result.request is None

    def test_duplicate_option_values_name_the_value(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("review", _findings([{"id": "B1", "summary": "a"}, {"id": "B1", "summary": "b"}]))
        spec = _spec(mode="select", options_from=_FINDINGS_FROM)

        result = resolve_request("triage", spec, "Fix?", artifact_store, _NO_SECRETS)

        assert result.status == InteractionResolveStatus.OPTIONS_DUPLICATE
        assert result.error_code == "ASK_OPTIONS_DUPLICATE"
        assert "'B1'" in result.errors[0]

    def test_select_with_fewer_than_two_items_reports_options_empty(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("review", _findings([{"id": "B1", "summary": "a"}]))
        spec = _spec(mode="select", options_from=_FINDINGS_FROM)

        result = resolve_request("triage", spec, "Fix?", artifact_store, _NO_SECRETS)

        assert result.status == InteractionResolveStatus.OPTIONS_EMPTY
        assert result.error_code == "ASK_OPTIONS_EMPTY"

    def test_unknown_display_artifact_reports_artifact_not_found(self, artifact_store: ArtifactStore) -> None:
        result = resolve_request("approve", _spec(artifact="missing"), "Go?", artifact_store, _NO_SECRETS)

        assert result.status == InteractionResolveStatus.ARTIFACT_NOT_FOUND
        assert result.error_code == "ASK_ARTIFACT_NOT_FOUND"
        assert result.errors == ["Artifact 'missing' was not found."]

    @pytest.mark.parametrize(
        ("line_count", "expected_tail"),
        [
            pytest.param(300, "Showing first 200 lines of PLAN.md (300 total)", id="over_limit"),
            pytest.param(200, "line 199", id="at_limit"),
        ],
    )
    def test_single_text_file_preview_truncates_at_200_lines_with_notice(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact, line_count: int, expected_tail: str
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "\n".join(f"line {n}" for n in range(line_count))})

        result = resolve_request("approve", _spec(artifact="plan"), "Go?", artifact_store, _NO_SECRETS)

        assert result.request is not None
        assert result.request.artifact is not None
        preview = result.request.artifact.preview
        assert preview[-1] == expected_tail
        assert len(preview) == (201 if line_count > 200 else 200)

    def test_multi_file_artifact_preview_lists_files_with_sizes(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan", {"A.md": "aaa", "B.md": "bb"})

        result = resolve_request("approve", _spec(artifact="plan"), "Go?", artifact_store, _NO_SECRETS)

        assert result.request is not None
        assert result.request.artifact is not None
        assert sorted(result.request.artifact.preview) == ["A.md (3 bytes)", "B.md (2 bytes)"]

    def test_preview_and_message_are_masked_with_the_redactor(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        redactor = SecretRedactor([("API_TOKEN", "s3cr3t-value")])
        publish_artifact("plan", {"PLAN.md": "use s3cr3t-value here"})

        result = resolve_request("approve", _spec(artifact="plan"), "Ship s3cr3t-value?", artifact_store, redactor)

        assert result.request is not None
        assert result.request.message == "Ship [REDACTED:API_TOKEN]?"
        assert result.request.artifact is not None
        assert result.request.artifact.preview == ["use [REDACTED:API_TOKEN] here"]
