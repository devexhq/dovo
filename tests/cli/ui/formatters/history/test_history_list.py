"""Tier 2 presentation contract tests for HistoryListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.history.common import format_session_duration
from dovo.cli.ui.formatters.history.history_list import HistoryListFormatter
from dovo.cli.ui.formatters.history.history_views import HistoryListView, SessionSummaryView
from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.sessions import HistoryListResult, HistoryListStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _sample_session_record(
    *,
    session_id: str = "sess-12345678",
    blueprint_name: str = "deploy-blueprint",
    status: SessionStatus = SessionStatus.COMPLETED,
    branch_name: str | None = "feature/test",
    started_at: str | None = "2026-08-19 01:00:00",
    completed_at: str | None = "2026-08-19 01:00:10",
    error_message: str | None = None,
) -> SessionRecord:
    return SessionRecord(
        id=1,
        session_id=session_id,
        blueprint_key=blueprint_name,
        blueprint_name=blueprint_name,
        status=status,
        branch_name=branch_name or "",
        started_at=started_at,
        completed_at=completed_at,
        error_message=error_message,
    )


def _make_session_summary_view(**overrides: Any) -> SessionSummaryView:
    defaults: dict[str, Any] = {
        "session_id": "sess-12345678",
        "blueprint_name": "deploy-blueprint",
        "status": "completed",
        "branch_name": "feature/test",
        "started_at": "2026-08-19 01:00:00",
        "completed_at": "2026-08-19 01:00:10",
        "duration_seconds": 10.0,
        "error_message": None,
    }
    defaults.update(overrides)
    return SessionSummaryView(**defaults)


def _make_history_list_view(**overrides: Any) -> HistoryListView:
    defaults: dict[str, Any] = {
        "status": HistoryListStatus.OK,
        "sessions": [_make_session_summary_view()],
        "total_sessions": 1,
        "errors": [],
        "warnings": [],
        "fixes": [],
    }
    defaults.update(overrides)
    return HistoryListView(**defaults)


POPULATED_RUNS = FormatterCase(
    data=HistoryListResult(status=HistoryListStatus.OK, sessions=[_sample_session_record()]),
    view=_make_history_list_view(),
    render_expectations=["sess-12345678", "deploy-blueprint", format_session_duration(10.0)],
)

EMPTY_RUNS = FormatterCase(
    data=HistoryListResult(status=HistoryListStatus.OK, sessions=[]),
    view=_make_history_list_view(sessions=[], total_sessions=0),
    render_expectations=[],
)

WARNINGS_RUNS = FormatterCase(
    data=HistoryListResult(
        status=HistoryListStatus.OK,
        sessions=[_sample_session_record()],
        warnings=["Reconciled 1 interrupted session (session_id: sess-stale)."],
    ),
    view=_make_history_list_view(warnings=["Reconciled 1 interrupted session (session_id: sess-stale)."]),
    render_expectations=[
        "sess-12345678",
        "deploy-blueprint",
        format_session_duration(10.0),
        "Reconciled 1 interrupted session (session_id: sess-stale).",
    ],
)

ERRORS_RUNS = FormatterCase(
    data=HistoryListResult(status=HistoryListStatus.OK, errors=["Database query failed."]),
    view=_make_history_list_view(sessions=[], total_sessions=0, errors=["Database query failed."]),
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
        POPULATED_RUNS,
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
            "warnings": [],
            "fixes": [],
        },
        id="populated_runs",
    ),
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
    pytest.param(
        ERRORS_RUNS,
        {
            "status": "ok",
            "sessions": [],
            "total_sessions": 0,
            "errors": ["Database query failed."],
            "warnings": [],
            "fixes": [],
        },
        id="errors_runs",
    ),
]


class HistoryListFormatterTests:
    """Tier 2 presentation contract tests for HistoryListFormatter."""

    @pytest.mark.parametrize("case", HISTORY_LIST_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[HistoryListResult, HistoryListView]) -> None:
        """Verify transform derives the expected HistoryListView."""
        assert_transform_derives_expected_view(HistoryListFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), HISTORY_LIST_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[HistoryListResult, HistoryListView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(HistoryListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", HISTORY_LIST_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[HistoryListResult, HistoryListView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(HistoryListFormatter, case.data, case.render_expectations)
