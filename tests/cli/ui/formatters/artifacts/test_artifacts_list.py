"""Tier 2 presentation contract tests for ArtifactsListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.artifacts.artifacts_list import ArtifactsListFormatter
from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactRowView, ArtifactsListView
from dovo.core.artifacts.models import ArtifactsListResult, ArtifactsListStatus
from dovo.core.db.models import ArtifactRecord
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

_DIGEST = "b07d44aacccccccccccccccccccccccccccccccccccccccccccccccccccccccc"

_RECORD = ArtifactRecord(
    project_id="proj-a",
    session_id="wf_abc123",
    name="dist-packages",
    path="/tmp/artifacts/wf_abc123/dist-packages/2",
    revision=2,
    digest=_DIGEST,
    step_id="plan",
    attempt=1,
    loop_iteration=2,
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
                revision=2,
                digest=_DIGEST,
                digest_display="b07d44aa",
                step_id="plan",
                step_display="plan",
                attempt=1,
                loop_iteration=2,
                iteration_display="2",
                file_count=3,
                size_bytes=1_070_546,
                size_display="1.02 MB",
                expires_at=None,
            ),
            ArtifactRowView(
                name="test-reports",
                session_id="wf_def456",
                revision=1,
                digest=None,
                digest_display="-",
                step_id=None,
                step_display="-",
                attempt=None,
                loop_iteration=None,
                iteration_display="-",
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
    view=ArtifactsListView(artifacts=[WITH_MULTIPLE_ARTIFACTS.view.artifacts[0]]),
)

WITH_LEGACY_ARTIFACT = FormatterCase(
    data=ArtifactsListResult(status=ArtifactsListStatus.OK, artifacts=[_SECOND_RECORD]),
    view=ArtifactsListView(artifacts=[WITH_MULTIPLE_ARTIFACTS.view.artifacts[1]]),
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
                    "revision": 2,
                    "digest": _DIGEST,
                    "digest_display": "b07d44aa",
                    "step_id": "plan",
                    "step_display": "plan",
                    "attempt": 1,
                    "loop_iteration": 2,
                    "iteration_display": "2",
                    "file_count": 3,
                    "size_bytes": 1_070_546,
                    "size_display": "1.02 MB",
                    "expires_at": None,
                }
            ],
            "errors": [],
            "warnings": [],
            "remediations": [],
        },
        id="populated",
    ),
    pytest.param(
        WITH_LEGACY_ARTIFACT,
        {
            "status": "ok",
            "artifacts": [
                {
                    "name": "test-reports",
                    "session_id": "wf_def456",
                    "revision": 1,
                    "digest": None,
                    "digest_display": "-",
                    "step_id": None,
                    "step_display": "-",
                    "attempt": None,
                    "loop_iteration": None,
                    "iteration_display": "-",
                    "file_count": 1,
                    "size_bytes": 2_048,
                    "size_display": "2.00 KB",
                    "expires_at": None,
                }
            ],
            "errors": [],
            "warnings": [],
            "remediations": [],
        },
        id="legacy_row",
    ),
    pytest.param(
        EMPTY,
        {"status": "ok", "artifacts": [], "errors": [], "warnings": [], "remediations": []},
        id="empty",
    ),
]


class ArtifactsListFormatterTests:
    """Tier 2 presentation contract tests for ArtifactsListFormatter."""

    def test_transform_preserves_every_artifact_row(self) -> None:
        assert_transform_derives_expected_view(
            ArtifactsListFormatter, WITH_MULTIPLE_ARTIFACTS.data, WITH_MULTIPLE_ARTIFACTS.view
        )

    def test_to_rich_renders_every_artifact_row(self) -> None:
        assert_rich_render_shows_every_view_value(
            ArtifactsListFormatter,
            WITH_MULTIPLE_ARTIFACTS.data,
            ["dist-packages", "wf_abc123", "test-reports", "wf_def456", "b07d44aa"],
        )

    @pytest.mark.parametrize(("case", "expected_payload"), ARTIFACTS_LIST_PAYLOAD_CASES)
    def test_to_json_serializable_matches_literal_populated_and_empty_dicts(
        self, case: FormatterCase[ArtifactsListResult, ArtifactsListView], expected_payload: dict[str, Any]
    ) -> None:
        assert_json_payload_matches_published_shape(ArtifactsListFormatter, case, expected_payload)
