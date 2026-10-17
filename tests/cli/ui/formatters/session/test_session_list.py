"""Tier 2 presentation contract tests for SessionListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.session.common import format_session_duration
from dovo.cli.ui.formatters.session.session_list import SessionListFormatter
from dovo.cli.ui.formatters.session.session_views import SessionListView
from dovo.core.sessions import HistoryListResult, HistoryListStatus
from tests.harness.builders.sessions import session_record, session_summary_view
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _make_session_list_view(**overrides: Any) -> SessionListView:
    defaults: dict[str, Any] = {
        "status": HistoryListStatus.OK,
        "sessions": [session_summary_view()],
        "total_sessions": 1,
        "errors": [],
        "warnings": [],
        "fixes": [],
    }
    defaults.update(overrides)
    return SessionListView(**defaults)


POPULATED_RUNS = FormatterCase(
    data=HistoryListResult(status=HistoryListStatus.OK, sessions=[session_record()]),
    view=_make_session_list_view(),
    render_expectations=["sess-12345678", "deploy-blueprint", format_session_duration(10.0)],
)

EMPTY_RUNS = FormatterCase(
    data=HistoryListResult(status=HistoryListStatus.OK, sessions=[]),
    view=_make_session_list_view(sessions=[], total_sessions=0),
    render_expectations=["No execution history found."],
)

WARNINGS_RUNS = FormatterCase(
    data=HistoryListResult(
        status=HistoryListStatus.OK,
        sessions=[session_record()],
        warnings=["Reconciled 1 interrupted session (session_id: sess-stale)."],
    ),
    view=_make_session_list_view(warnings=["Reconciled 1 interrupted session (session_id: sess-stale)."]),
    render_expectations=[
        "sess-12345678",
        "deploy-blueprint",
        format_session_duration(10.0),
        "Reconciled 1 interrupted session (session_id: sess-stale).",
    ],
)

ERRORS_RUNS = FormatterCase(
    data=HistoryListResult(status=HistoryListStatus.OK, errors=["Database query failed."]),
    view=_make_session_list_view(sessions=[], total_sessions=0, errors=["Database query failed."]),
    render_expectations=["Database query failed."],
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
            "status": "ok",
            "sessions": [],
            "total_sessions": 0,
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="empty_runs",
    ),
    pytest.param(
        WARNINGS_RUNS,
        {
            "status": "ok",
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
            "errors": [],
            "warnings": ["Reconciled 1 interrupted session (session_id: sess-stale)."],
            "fixes": [],
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
