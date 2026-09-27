"""Tier 2 presentation contract tests for ArtifactsListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)
from worktree.cli.ui.formatters.artifacts.artifacts_list import ArtifactsListFormatter
from worktree.cli.ui.formatters.artifacts.artifacts_views import ArtifactRowView, ArtifactsListView
from worktree.core.artifacts.models import ArtifactsListResult, ArtifactsListStatus
from worktree.core.db.models import ArtifactRecord

_RECORD = ArtifactRecord(
    project_id="proj-a",
    session_id="wf_abc123",
    name="dist-packages",
    path="/tmp/artifacts/wf_abc123/dist-packages",
    size_bytes=1_070_546,
    file_count=3,
    created_at="2026-08-31T20:00:00Z",
    expires_at=None,
)

_SECOND_RECORD = ArtifactRecord(
    project_id="proj-a",
    session_id="wf_def456",
    name="test-reports",
    path="/tmp/artifacts/wf_def456/test-reports",
    size_bytes=2_048,
    file_count=1,
    created_at="2026-09-01T10:00:00Z",
    expires_at=None,
)

WITH_MULTIPLE_ARTIFACTS = FormatterCase(
    data=ArtifactsListResult(status=ArtifactsListStatus.OK, artifacts=[_RECORD, _SECOND_RECORD]),
    view=ArtifactsListView(
        artifacts=[
            ArtifactRowView(
                name="dist-packages",
                session_id="wf_abc123",
                file_count=3,
                size_bytes=1_070_546,
                size_display="1.02 MB",
                expires_at=None,
            ),
            ArtifactRowView(
                name="test-reports",
                session_id="wf_def456",
                file_count=1,
                size_bytes=2_048,
                size_display="2.00 KB",
                expires_at=None,
            ),
        ]
    ),
)

WITH_ARTIFACT = FormatterCase(
    data=ArtifactsListResult(status=ArtifactsListStatus.OK, artifacts=[_RECORD]),
    view=ArtifactsListView(
        artifacts=[
            ArtifactRowView(
                name="dist-packages",
                session_id="wf_abc123",
                file_count=3,
                size_bytes=1_070_546,
                size_display="1.02 MB",
                expires_at=None,
            )
        ]
    ),
)

EMPTY = FormatterCase(
    data=ArtifactsListResult(status=ArtifactsListStatus.OK, artifacts=[]),
    view=ArtifactsListView(artifacts=[]),
)

ARTIFACTS_LIST_PAYLOAD_CASES = [
    pytest.param(
        WITH_ARTIFACT,
        {
            "status": "ok",
            "artifacts": [
                {
                    "name": "dist-packages",
                    "session_id": "wf_abc123",
                    "file_count": 3,
                    "size_bytes": 1_070_546,
                    "size_display": "1.02 MB",
                    "expires_at": None,
                }
            ],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="populated",
    ),
    pytest.param(
        EMPTY,
        {"status": "ok", "artifacts": [], "errors": [], "warnings": [], "fixes": []},
        id="empty",
    ),
]


class ArtifactsListFormatterTests:
    """Tier 2 presentation contract tests for ArtifactsListFormatter."""

    def test_transform_derives_human_readable_size(self) -> None:
        """[tier-2/unit] ArtifactsListFormatter.transform: an ArtifactRecord with size_bytes=1_070_546 maps to ArtifactsListView.size_display == '1.02 MB'."""
        assert_transform_derives_expected_view(ArtifactsListFormatter, WITH_ARTIFACT.data, WITH_ARTIFACT.view)

    def test_transform_preserves_every_artifact_row(self) -> None:
        """[tier-2/unit] ArtifactsListFormatter.transform: two ArtifactRecords produce two ArtifactRowViews, not just the first."""
        assert_transform_derives_expected_view(
            ArtifactsListFormatter, WITH_MULTIPLE_ARTIFACTS.data, WITH_MULTIPLE_ARTIFACTS.view
        )

    def test_to_rich_renders_every_artifact_row(self) -> None:
        """[tier-2/unit] ArtifactsListFormatter.to_rich: a two-artifact result renders both rows' names and session_ids."""
        assert_rich_render_shows_every_view_value(
            ArtifactsListFormatter,
            WITH_MULTIPLE_ARTIFACTS.data,
            ["dist-packages", "wf_abc123", "test-reports", "wf_def456"],
        )

    @pytest.mark.parametrize(("case", "expected_payload"), ARTIFACTS_LIST_PAYLOAD_CASES)
    def test_to_json_serializable_matches_literal_populated_and_empty_dicts(
        self, case: FormatterCase[ArtifactsListResult, ArtifactsListView], expected_payload: dict[str, Any]
    ) -> None:
        """[tier-2/unit] ArtifactsListFormatter.to_json_serializable: a populated ArtifactsListResult and an empty one both match their exact literal dict wire payloads."""
        assert_json_payload_matches_published_shape(ArtifactsListFormatter, case, expected_payload)

    def test_to_rich_renders_pinned_artifact_name_and_session_id(self) -> None:
        """[tier-2/unit] ArtifactsListFormatter.to_rich: render_rich(..., width=160) output contains the artifact's name and session_id values, not any panel caption."""
        assert_rich_render_shows_every_view_value(
            ArtifactsListFormatter, WITH_ARTIFACT.data, ["dist-packages", "wf_abc123"]
        )
