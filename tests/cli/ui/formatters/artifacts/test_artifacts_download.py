"""Tier 2 presentation contract tests for ArtifactDownloadFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.artifacts.artifacts_download import ArtifactDownloadFormatter
from dovo.core.artifacts.models import ArtifactDownloadResult, ArtifactDownloadStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

OK = FormatterCase(
    data=ArtifactDownloadResult(
        status=ArtifactDownloadStatus.OK,
        session_id="wf_abc123",
        name="dist-packages",
        dest="/tmp/out",
        file_count=2,
    ),
    view=ArtifactDownloadResult(
        status=ArtifactDownloadStatus.OK,
        session_id="wf_abc123",
        name="dist-packages",
        dest="/tmp/out",
        file_count=2,
    ),
    render_expectations=["dist-packages", "/tmp/out"],
)

NOT_FOUND = FormatterCase(
    data=ArtifactDownloadResult(
        status=ArtifactDownloadStatus.NOT_FOUND,
        session_id="wf_abc123",
        name="missing",
        errors=["Artifact 'missing' not found for session 'wf_abc123'"],
    ),
    view=ArtifactDownloadResult(
        status=ArtifactDownloadStatus.NOT_FOUND,
        session_id="wf_abc123",
        name="missing",
        errors=["Artifact 'missing' not found for session 'wf_abc123'"],
    ),
    render_expectations=["Artifact 'missing' not found for session 'wf_abc123'"],
)

ARTIFACT_DOWNLOAD_CASES = [
    pytest.param(OK, id="ok"),
    pytest.param(NOT_FOUND, id="not_found"),
]

ARTIFACT_DOWNLOAD_PAYLOAD_CASES = [
    pytest.param(
        OK,
        {
            "status": "ok",
            "session_id": "wf_abc123",
            "name": "dist-packages",
            "dest": "/tmp/out",
            "file_count": 2,
            "errors": [],
            "warnings": [],
            "error_code": None,
            "fixes": [],
        },
        id="ok",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "session_id": "wf_abc123",
            "name": "missing",
            "dest": "",
            "file_count": 0,
            "errors": ["Artifact 'missing' not found for session 'wf_abc123'"],
            "warnings": [],
            "error_code": None,
            "fixes": [],
        },
        id="not_found",
    ),
]


class ArtifactDownloadFormatterTests:
    """Tier 2 presentation contract tests for ArtifactDownloadFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), ARTIFACT_DOWNLOAD_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ArtifactDownloadResult, ArtifactDownloadResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(ArtifactDownloadFormatter, case, expected_payload)

    @pytest.mark.parametrize("case", ARTIFACT_DOWNLOAD_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[ArtifactDownloadResult, ArtifactDownloadResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(ArtifactDownloadFormatter, case, case.render_expectations)
