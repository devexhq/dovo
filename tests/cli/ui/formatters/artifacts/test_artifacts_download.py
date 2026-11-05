"""Tier 2 presentation contract tests for ArtifactDownloadFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.artifacts.artifacts_download import ArtifactDownloadFormatter
from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactDownloadView
from dovo.common.error_codes import ErrorCode
from dovo.core.artifacts.models import ArtifactDownloadResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

OK = FormatterCase(
    data=ArtifactDownloadResult(
        session_id="wf_abc123",
        name="dist-packages",
        dest="/tmp/out",
        revision=2,
        file_count=2,
    ),
    view=ArtifactDownloadView(
        ok=True, session_id="wf_abc123", name="dist-packages", dest="/tmp/out", revision=2, file_count=2
    ),
    render_expectations=["dist-packages", "/tmp/out"],
)

NOT_FOUND = FormatterCase(
    data=ArtifactDownloadResult(
        session_id="wf_abc123",
        name="missing",
        error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        errors=["Artifact 'missing' not found for session 'wf_abc123'"],
    ),
    view=ArtifactDownloadView(
        ok=False,
        error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        errors=["Artifact 'missing' not found for session 'wf_abc123'"],
        session_id="wf_abc123",
        name="missing",
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
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "session_id": "wf_abc123",
            "name": "dist-packages",
            "dest": "/tmp/out",
            "revision": 2,
            "file_count": 2,
        },
        id="ok",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "ok": False,
            "error_code": "ARTIFACT_NOT_FOUND",
            "errors": ["Artifact 'missing' not found for session 'wf_abc123'"],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "session_id": "wf_abc123",
            "name": "missing",
            "dest": "",
            "revision": 0,
            "file_count": 0,
        },
        id="not_found",
    ),
]


class ArtifactDownloadFormatterTests:
    """Tier 2 presentation contract tests for ArtifactDownloadFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), ARTIFACT_DOWNLOAD_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ArtifactDownloadResult, ArtifactDownloadView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(ArtifactDownloadFormatter, case, expected_payload)

    @pytest.mark.parametrize("case", ARTIFACT_DOWNLOAD_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[ArtifactDownloadResult, ArtifactDownloadView]
    ) -> None:
        assert_rich_render_shows_every_view_value(ArtifactDownloadFormatter, case, case.render_expectations)
