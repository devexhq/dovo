"""Tier 2 presentation contract tests for SessionListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.session.common import format_session_duration
from dovo.cli.ui.formatters.session.session_list import SessionListFormatter
from dovo.cli.ui.formatters.session.session_views import SessionListView
from dovo.common.error_codes import ErrorCode
from dovo.core.sessions import HistoryListResult
from tests.harness.builders.sessions import session_record, session_summary_view
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _make_session_list_view(**overrides: object) -> SessionListView:
    defaults: dict[str, object] = {
        "ok": overrides.get("error_code") is None,
        "sessions": [session_summary_view()],
        "total_sessions": 1,
        "errors": [],
        "warnings": [],
        "remediations": [],
        "error_code": None,
        "diagnostics": [],
    }
    return SessionListView.model_validate({**defaults, **overrides})


POPULATED_RUNS = FormatterCase(
    data=HistoryListResult(sessions=[session_record()]),
    view=_make_session_list_view(),
    render_expectations=["sess-12345678", "deploy-blueprint", format_session_duration(10.0)],
)

EMPTY_RUNS = FormatterCase(
    data=HistoryListResult(sessions=[]),
    view=_make_session_list_view(sessions=[], total_sessions=0),
    render_expectations=["No execution history found."],
)

WARNINGS_RUNS = FormatterCase(
    data=HistoryListResult(
        sessions=[session_record()],
        warnings=["Marked 1 interrupted session failed (session_id: sess-stale)."],
    ),
    view=_make_session_list_view(warnings=["Marked 1 interrupted session failed (session_id: sess-stale)."]),
    render_expectations=[
        "sess-12345678",
        "deploy-blueprint",
        format_session_duration(10.0),
        "Marked 1 interrupted session failed (session_id: sess-stale).",
    ],
)

ERRORS_RUNS = FormatterCase(
    data=HistoryListResult(error_code=ErrorCode.INTERNAL_ERROR, errors=["Database query failed."]),
    view=_make_session_list_view(
        sessions=[], total_sessions=0, error_code=ErrorCode.INTERNAL_ERROR, errors=["Database query failed."]
    ),
    render_expectations=["Session List Failed", "Database query failed."],
)

HISTORY_LIST_CASES = [
    pytest.param(POPULATED_RUNS, id="populated_runs"),
    pytest.param(EMPTY_RUNS, id="empty_runs"),
    pytest.param(WARNINGS_RUNS, id="warnings_runs"),
    pytest.param(ERRORS_RUNS, id="errors_runs"),
]

HISTORY_LIST_PAYLOAD_CASES = [
    pytest.param(
        EMPTY_RUNS,
        {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "sessions": [],
            "total_sessions": 0,
        },
        id="empty_runs",
    ),
    pytest.param(
        ERRORS_RUNS,
        {
            "ok": False,
            "error_code": "INTERNAL_ERROR",
            "errors": ["Database query failed."],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "sessions": [],
            "total_sessions": 0,
        },
        id="errors_runs",
    ),
    pytest.param(
        WARNINGS_RUNS,
        {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": ["Marked 1 interrupted session failed (session_id: sess-stale)."],
            "remediations": [],
            "diagnostics": [],
            "sessions": [
                {
                    "session_id": "sess-12345678",
                    "blueprint_name": "deploy-blueprint",
                    "status": "completed",
                    "branch_name": "feature/test",
                    "started_at": "2026-08-19 01:00:00",
                    "completed_at": "2026-08-19 01:00:10",
                    "duration_seconds": 10.0,
                    "error_message": None,
                }
            ],
            "total_sessions": 1,
        },
        id="warnings_runs",
    ),
]


class SessionListFormatterTests:
    """Tier 2 presentation contract tests for SessionListFormatter."""

    @pytest.mark.parametrize("case", HISTORY_LIST_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[HistoryListResult, SessionListView]) -> None:
        assert_transform_derives_expected_view(SessionListFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), HISTORY_LIST_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[HistoryListResult, SessionListView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(SessionListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", HISTORY_LIST_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[HistoryListResult, SessionListView]) -> None:
        assert_rich_render_shows_every_view_value(SessionListFormatter, case.data, case.render_expectations)
