"""Tier 2 presentation contract tests for StepOutputFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import StepOutputEvent
from dovo.cli.ui.formatters.events.step_output import StepOutputFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

STDOUT_LINE = FormatterCase(
    data=StepOutputEvent(step_id="step-1", line="building...", stream="stdout"),
    view=StepOutputEvent(step_id="step-1", line="building...", stream="stdout"),
    render_expectations=["building..."],
)

STDERR_LINE = FormatterCase(
    data=StepOutputEvent(step_id="step-1", line="warning: deprecated", stream="stderr"),
    view=StepOutputEvent(step_id="step-1", line="warning: deprecated", stream="stderr"),
    render_expectations=["warning: deprecated"],
)

STEP_OUTPUT_CASES = [
    pytest.param(STDOUT_LINE, id="stdout_line"),
    pytest.param(STDERR_LINE, id="stderr_line"),
]

STEP_OUTPUT_PAYLOAD_CASES = [
    pytest.param(
        STDOUT_LINE,
        {
            "step_id": "step-1",
            "line": "building...",
            "stream": "stdout",
        },
        id="stdout_line",
    ),
    pytest.param(
        STDERR_LINE,
        {
            "step_id": "step-1",
            "line": "warning: deprecated",
            "stream": "stderr",
        },
        id="stderr_line",
    ),
]


class StepOutputFormatterTests:
    """Tier 2 presentation contract tests for StepOutputFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), STEP_OUTPUT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[StepOutputEvent, StepOutputEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(StepOutputFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", STEP_OUTPUT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[StepOutputEvent, StepOutputEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(StepOutputFormatter, case.data, case.render_expectations)
