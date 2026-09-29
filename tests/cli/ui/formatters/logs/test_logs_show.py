"""Tier 2 presentation contract tests for LogsShowFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)
from worktree.cli.ui.formatters.logs.logs_show import LogsShowFormatter
from worktree.cli.ui.formatters.logs.logs_views import LogsShowView
from worktree.core.logs import RunLogEvent, RunLogEventType
from worktree.core.logs.models import LogsShowResult, LogsShowStatus

_STEP_EVENT = RunLogEvent(
    ts="2026-09-26T10:00:00+00:00", event=RunLogEventType.STEP_START, step_index=1, step_id="build", attempt=1
)

EVENTS_MODE = FormatterCase(
    data=LogsShowResult(status=LogsShowStatus.OK, session_id="sess-1", events=[_STEP_EVENT]),
    view=LogsShowView(status=LogsShowStatus.OK, session_id="sess-1", events=[_STEP_EVENT]),
    render_expectations=["2026-09-26T10:00:00+00:00", "step_start", "build"],
)

LINES_MODE = FormatterCase(
    data=LogsShowResult(status=LogsShowStatus.OK, session_id="sess-1", lines=["pytest tests/", "3 passed"]),
    view=LogsShowView(status=LogsShowStatus.OK, session_id="sess-1", lines=["pytest tests/", "3 passed"]),
    render_expectations=["pytest tests/", "3 passed"],
)

SESSION_NOT_FOUND = FormatterCase(
    data=LogsShowResult(status=LogsShowStatus.SESSION_NOT_FOUND, session_id="ghost"),
    view=LogsShowView(status=LogsShowStatus.SESSION_NOT_FOUND, session_id="ghost"),
    render_expectations=["ghost"],
)

STEP_NOT_FOUND = FormatterCase(
    data=LogsShowResult(status=LogsShowStatus.STEP_NOT_FOUND, session_id="sess-1", available_steps=["build", "lint"]),
    view=LogsShowView(status=LogsShowStatus.STEP_NOT_FOUND, session_id="sess-1", available_steps=["build", "lint"]),
    render_expectations=["build", "lint"],
)

ATTEMPT_NOT_FOUND = FormatterCase(
    data=LogsShowResult(status=LogsShowStatus.ATTEMPT_NOT_FOUND, session_id="sess-1", available_attempts=[1, 2]),
    view=LogsShowView(status=LogsShowStatus.ATTEMPT_NOT_FOUND, session_id="sess-1", available_attempts=[1, 2]),
    render_expectations=["1, 2"],
)

LOGS_SHOW_CASES = [
    pytest.param(EVENTS_MODE, id="events_mode"),
    pytest.param(LINES_MODE, id="lines_mode"),
    pytest.param(SESSION_NOT_FOUND, id="session_not_found"),
    pytest.param(STEP_NOT_FOUND, id="step_not_found"),
    pytest.param(ATTEMPT_NOT_FOUND, id="attempt_not_found"),
]

_EMPTY_ENVELOPE: dict[str, Any] = {
    "lines": [],
    "events": [],
    "available_steps": [],
    "available_attempts": [],
    "errors": [],
    "warnings": [],
    "fixes": [],
}

LOGS_SHOW_PAYLOAD_CASES = [
    pytest.param(
        EVENTS_MODE,
        {
            **_EMPTY_ENVELOPE,
            "status": "ok",
            "session_id": "sess-1",
            "events": [
                {
                    "ts": "2026-09-26T10:00:00+00:00",
                    "event": "step_start",
                    "session_id": None,
                    "blueprint_key": None,
                    "step_index": 1,
                    "step_id": "build",
                    "attempt": 1,
                    "status": None,
                    "exit_code": None,
                    "loop_id": None,
                    "turn": None,
                    "max_iterations": None,
                    "all_passed": None,
                    "next_turn": None,
                    "conditions": None,
                }
            ],
        },
        id="events_mode",
    ),
    pytest.param(
        LINES_MODE,
        {**_EMPTY_ENVELOPE, "status": "ok", "session_id": "sess-1", "lines": ["pytest tests/", "3 passed"]},
        id="lines_mode",
    ),
    pytest.param(
        SESSION_NOT_FOUND,
        {**_EMPTY_ENVELOPE, "status": "session_not_found", "session_id": "ghost"},
        id="session_not_found",
    ),
]


class LogsShowFormatterTests:
    """Tier 2 presentation contract tests for LogsShowFormatter."""

    @pytest.mark.parametrize("case", LOGS_SHOW_CASES)
    def test_transform_maps_result_to_view(self, case: FormatterCase[LogsShowResult, LogsShowView]) -> None:
        """LogsShowFormatter.transform maps LogsShowResult field-for-field to LogsShowView."""
        assert_transform_derives_expected_view(LogsShowFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), LOGS_SHOW_PAYLOAD_CASES)
    def test_json_wire_matches_literal_dict_for_populated_and_empty_states(
        self, case: FormatterCase[LogsShowResult, LogsShowView], expected_payload: dict[str, Any]
    ) -> None:
        """LogsShowFormatter.to_json_serializable equals the literal wire dict for populated and sparse states."""
        assert_json_payload_matches_published_shape(LogsShowFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", LOGS_SHOW_CASES)
    def test_rich_render_shows_only_view_sourced_values(
        self, case: FormatterCase[LogsShowResult, LogsShowView]
    ) -> None:
        """LogsShowFormatter.to_rich renders the case's view values at width 160."""
        assert_rich_render_shows_every_view_value(LogsShowFormatter, case.data, case.render_expectations)
