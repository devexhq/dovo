"""Tier 2 presentation contract tests for RunSuccessFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import RunSuccessEvent
from dovo.cli.ui.formatters.events.run_success import RunSuccessFormatter
from dovo.core.db import SessionStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

FIRST_COMPLETED = FormatterCase(
    data=RunSuccessEvent(session_id="sess_123", blueprint_name="my_blueprint", status=SessionStatus.COMPLETED),
    view=RunSuccessEvent(session_id="sess_123", blueprint_name="my_blueprint", status=SessionStatus.COMPLETED),
    render_expectations=["my_blueprint", "sess_123", "completed"],
)

SECOND_COMPLETED = FormatterCase(
    data=RunSuccessEvent(session_id="sess_456", blueprint_name="deploy-flow", status=SessionStatus.COMPLETED),
    view=RunSuccessEvent(session_id="sess_456", blueprint_name="deploy-flow", status=SessionStatus.COMPLETED),
    render_expectations=["deploy-flow", "sess_456", "completed"],
)

RUN_SUCCESS_CASES = [
    pytest.param(FIRST_COMPLETED, id="first_completed"),
    pytest.param(SECOND_COMPLETED, id="second_completed"),
]

RUN_SUCCESS_PAYLOAD_CASES = [
    pytest.param(
        FIRST_COMPLETED,
        {
            "session_id": "sess_123",
            "blueprint_name": "my_blueprint",
            "status": "completed",
        },
        id="first_completed",
    ),
    pytest.param(
        SECOND_COMPLETED,
        {
            "session_id": "sess_456",
            "blueprint_name": "deploy-flow",
            "status": "completed",
        },
        id="second_completed",
    ),
]


class RunSuccessFormatterTests:
    """Tier 2 presentation contract tests for RunSuccessFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), RUN_SUCCESS_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[RunSuccessEvent, RunSuccessEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(RunSuccessFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", RUN_SUCCESS_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[RunSuccessEvent, RunSuccessEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(RunSuccessFormatter, case.data, case.render_expectations)
