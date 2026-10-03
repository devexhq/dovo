"""Tier 2 presentation contract tests for StepDoneFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import StepDoneEvent
from dovo.cli.ui.formatters.events.step_done import StepDoneFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

STEP_SUCCESS = FormatterCase(
    data=StepDoneEvent(
        idx=1,
        total=3,
        step_id="step-1",
        ok=True,
        exit_code=0,
        duration_seconds=1.5,
        error_message=None,
    ),
    view=StepDoneEvent(
        idx=1,
        total=3,
        step_id="step-1",
        ok=True,
        exit_code=0,
        duration_seconds=1.5,
        error_message=None,
    ),
    render_expectations=["step-1", "1", "3"],
)

STEP_FAILURE = FormatterCase(
    data=StepDoneEvent(
        idx=2,
        total=3,
        step_id="step-2",
        ok=False,
        exit_code=1,
        duration_seconds=0.8,
        error_message="Process crashed",
    ),
    view=StepDoneEvent(
        idx=2,
        total=3,
        step_id="step-2",
        ok=False,
        exit_code=1,
        duration_seconds=0.8,
        error_message="Process crashed",
    ),
    render_expectations=["step-2", "2", "3", "Process crashed"],
)

STEP_DONE_CASES = [
    pytest.param(STEP_SUCCESS, id="step_success"),
    pytest.param(STEP_FAILURE, id="step_failure"),
]

STEP_DONE_PAYLOAD_CASES = [
    pytest.param(
        STEP_SUCCESS,
        {
            "idx": 1,
            "total": 3,
            "step_id": "step-1",
            "ok": True,
            "exit_code": 0,
            "duration_seconds": 1.5,
            "error_message": None,
        },
        id="step_success",
    ),
    pytest.param(
        STEP_FAILURE,
        {
            "idx": 2,
            "total": 3,
            "step_id": "step-2",
            "ok": False,
            "exit_code": 1,
            "duration_seconds": 0.8,
            "error_message": "Process crashed",
        },
        id="step_failure",
    ),
]


class StepDoneFormatterTests:
    """Tier 2 presentation contract tests for StepDoneFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), STEP_DONE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[StepDoneEvent, StepDoneEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(StepDoneFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", STEP_DONE_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[StepDoneEvent, StepDoneEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(StepDoneFormatter, case.data, case.render_expectations)
