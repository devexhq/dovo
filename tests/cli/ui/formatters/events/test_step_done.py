"""Tier 2 presentation contract tests for StepDoneFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import StepDoneEvent
from dovo.cli.ui.formatters.events.step_done import StepDoneFormatter
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

STEP_SUCCESS = FormatterCase.identity(
    data=StepDoneEvent(
        idx=1,
        total=3,
        step_id="step-1",
        status="completed",
        attempts=1,
        exit_code=0,
        duration_seconds=1.5,
        error_message=None,
    ),
    render_expectations=["step-1", "1", "3", "COMPLETED"],
)

STEP_FAILURE = FormatterCase.identity(
    data=StepDoneEvent(
        idx=2,
        total=3,
        step_id="step-2",
        status="failed",
        attempts=3,
        exit_code=1,
        duration_seconds=0.8,
        error_message="Process crashed",
        error_code=ErrorCode.STEP_COMMAND_FAILED,
        remediations=[Remediation(message="Run 'make fix'", command="make fix")],
        rejected_patch_path="/tmp/rejected.patch",
    ),
    render_expectations=["step-2", "2", "3", "Process crashed", "Fix:\n- Run 'make fix'"],
)

STEP_IGNORED = FormatterCase.identity(
    data=StepDoneEvent(
        idx=3,
        total=3,
        step_id="step-3",
        status="ignored",
        attempts=1,
        exit_code=1,
        duration_seconds=0.2,
        error_message="Lint failed",
        error_code=ErrorCode.STEP_COMMAND_FAILED,
    ),
    render_expectations=["step-3", "3", "COMPLETED"],
)

STEP_DONE_CASES = [
    pytest.param(STEP_SUCCESS, id="step_success"),
    pytest.param(STEP_FAILURE, id="step_failure"),
    pytest.param(STEP_IGNORED, id="step_ignored"),
]

STEP_DONE_PAYLOAD_CASES = [
    pytest.param(
        STEP_SUCCESS,
        {
            "idx": 1,
            "total": 3,
            "step_id": "step-1",
            "status": "completed",
            "attempts": 1,
            "exit_code": 0,
            "duration_seconds": 1.5,
            "error_message": None,
            "error_code": None,
            "remediations": [],
            "rejected_patch_path": None,
        },
        id="step_success",
    ),
    pytest.param(
        STEP_FAILURE,
        {
            "idx": 2,
            "total": 3,
            "step_id": "step-2",
            "status": "failed",
            "attempts": 3,
            "exit_code": 1,
            "duration_seconds": 0.8,
            "error_message": "Process crashed",
            "error_code": "STEP_COMMAND_FAILED",
            "remediations": [{"message": "Run 'make fix'", "command": "make fix"}],
            "rejected_patch_path": "/tmp/rejected.patch",
        },
        id="step_failure",
    ),
    pytest.param(
        STEP_IGNORED,
        {
            "idx": 3,
            "total": 3,
            "step_id": "step-3",
            "status": "ignored",
            "attempts": 1,
            "exit_code": 1,
            "duration_seconds": 0.2,
            "error_message": "Lint failed",
            "error_code": "STEP_COMMAND_FAILED",
            "remediations": [],
            "rejected_patch_path": None,
        },
        id="step_ignored",
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
        assert_json_payload_matches_published_shape(StepDoneFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", STEP_DONE_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[StepDoneEvent, StepDoneEvent]) -> None:
        assert_rich_render_shows_every_view_value(StepDoneFormatter, case.data, case.render_expectations)
