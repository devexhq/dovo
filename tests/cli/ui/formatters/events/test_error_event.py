"""Tier 2 presentation contract tests for ErrorEventFormatter."""

from __future__ import annotations

from pathlib import Path

from dovo.cli.ui.events import ErrorEvent
from dovo.cli.ui.formatters.events.error_event import ErrorEventFormatter, ErrorEventView
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

_RECORD = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de", log_path=Path("/logs/2026-10-19/exc_3f9a1c07b2de.json"), level=DiagnosticLevel.ERROR
)
_REMEDIATION = Remediation(message="Run 'dovo init' to initialize the workspace.", command="dovo init")

ERROR_EVENT = FormatterCase(
    data=ErrorEvent(
        title="Fatal Error",
        error_code=ErrorCode.INTERNAL_ERROR,
        errors=["A fatal unexpected error occurred. Details: boom", "Rollback failed."],
        remediations=[_REMEDIATION],
        diagnostics=[_RECORD],
    ),
    view=ErrorEventView(
        title="Fatal Error",
        error_code="INTERNAL_ERROR",
        errors=["A fatal unexpected error occurred. Details: boom", "Rollback failed."],
        remediations=[_REMEDIATION],
        diagnostics=[_RECORD],
    ),
    render_expectations=[
        "Fatal Error",
        "A fatal unexpected error occurred. Details: boom",
        "Rollback failed.",
        "Run 'dovo init' to initialize the workspace.",
        "Details logged: /logs/2026-10-19/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)


class ErrorEventFormatterTests:
    def test_transform_returns_header_view(self) -> None:
        assert_transform_derives_expected_view(ErrorEventFormatter, ERROR_EVENT.data, ERROR_EVENT.view)

    def test_json_is_exact_header(self) -> None:
        assert_json_payload_matches_published_shape(
            ErrorEventFormatter,
            ERROR_EVENT.data,
            {
                "ok": False,
                "error_code": "INTERNAL_ERROR",
                "errors": ["A fatal unexpected error occurred. Details: boom", "Rollback failed."],
                "warnings": [],
                "remediations": [{"message": "Run 'dovo init' to initialize the workspace.", "command": "dovo init"}],
                "diagnostics": [{"event_id": "exc_3f9a1c07b2de", "log_path": "/logs/2026-10-19/exc_3f9a1c07b2de.json"}],
            },
        )

    def test_rich_render_shows_every_view_value(self) -> None:
        assert_rich_render_shows_every_view_value(
            ErrorEventFormatter, ERROR_EVENT.data, ERROR_EVENT.render_expectations
        )
