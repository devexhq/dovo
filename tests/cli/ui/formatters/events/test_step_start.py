"""Tier 2 presentation contract tests for StepStartFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import StepStartEvent
from dovo.cli.ui.formatters.events.step_start import StepStartFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

WITH_NAME_AND_COMMAND = FormatterCase(
    data=StepStartEvent(
        idx=1,
        total=3,
        step_id="step-1",
        name="Run Tests",
        command="pytest",
    ),
    view=StepStartEvent(
        idx=1,
        total=3,
        step_id="step-1",
        name="Run Tests",
        command="pytest",
    ),
    render_expectations=["1", "3", "Run Tests", "pytest"],
)

WITHOUT_NAME_AND_COMMAND = FormatterCase(
    data=StepStartEvent(
        idx=2,
        total=3,
        step_id="step-2",
        name=None,
        command=None,
    ),
    view=StepStartEvent(
        idx=2,
        total=3,
        step_id="step-2",
        name=None,
        command=None,
    ),
    render_expectations=["2", "3", "step-2"],
)

STEP_START_CASES = [
    pytest.param(WITH_NAME_AND_COMMAND, id="with_name_and_command"),
    pytest.param(WITHOUT_NAME_AND_COMMAND, id="without_name_and_command"),
]

STEP_START_PAYLOAD_CASES = [
    pytest.param(
        WITH_NAME_AND_COMMAND,
        {
            "idx": 1,
            "total": 3,
            "step_id": "step-1",
            "name": "Run Tests",
            "command": "pytest",
        },
        id="with_name_and_command",
    ),
    pytest.param(
        WITHOUT_NAME_AND_COMMAND,
        {
            "idx": 2,
            "total": 3,
            "step_id": "step-2",
            "name": None,
            "command": None,
        },
        id="without_name_and_command",
    ),
]


class StepStartFormatterTests:
    """Tier 2 presentation contract tests for StepStartFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), STEP_START_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[StepStartEvent, StepStartEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(StepStartFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", STEP_START_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[StepStartEvent, StepStartEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(StepStartFormatter, case.data, case.render_expectations)
