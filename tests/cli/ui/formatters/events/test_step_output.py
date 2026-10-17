"""Tier 2 presentation contract tests for StepOutputFormatter."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dovo.cli.ui.events import AgentSummaryView, StepOutputEvent
from dovo.cli.ui.formatters.events.step_output import StepOutputFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

SUCCESS_SUMMARY_LINE = json.dumps(
    {"status": "no_op", "summary": "Wrote the plan.", "unfixable_reason": None, "touched_files": ["notes.txt"]}
)
FAILURE_SUMMARY_LINE = json.dumps(
    {"status": "unfixable", "summary": None, "unfixable_reason": "no network", "touched_files": []}
)

STDOUT_LINE = FormatterCase.identity(
    data=StepOutputEvent(step_id="step-1", line="building...", stream="stdout"),
    render_expectations=["building..."],
)

STDERR_LINE = FormatterCase.identity(
    data=StepOutputEvent(step_id="step-1", line="warning: deprecated", stream="stderr"),
    render_expectations=["warning: deprecated"],
)

SUCCESS_SUMMARY = FormatterCase.identity(
    data=StepOutputEvent(
        step_id="plan",
        line=SUCCESS_SUMMARY_LINE,
        agent_summary=AgentSummaryView(
            status="no_op", succeeded=True, summary="Wrote the plan.", touched_files=["notes.txt"]
        ),
    ),
    render_expectations=["no_op", "Wrote the plan.", "notes.txt"],
)

FAILURE_SUMMARY = FormatterCase.identity(
    data=StepOutputEvent(
        step_id="plan",
        line=FAILURE_SUMMARY_LINE,
        agent_summary=AgentSummaryView(status="unfixable", succeeded=False, unfixable_reason="no network"),
    ),
    render_expectations=["unfixable", "no network"],
)

JSON_LINE_WITHOUT_SUMMARY = FormatterCase.identity(
    data=StepOutputEvent(step_id="step-1", line='{"status": "ok"}'),
    render_expectations=['{"status": "ok"}'],
)

STEP_OUTPUT_CASES = [
    pytest.param(STDOUT_LINE, id="stdout_line"),
    pytest.param(STDERR_LINE, id="stderr_line"),
    pytest.param(SUCCESS_SUMMARY, id="success_summary"),
    pytest.param(FAILURE_SUMMARY, id="failure_summary"),
    pytest.param(JSON_LINE_WITHOUT_SUMMARY, id="json_line_without_summary"),
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
    pytest.param(
        SUCCESS_SUMMARY,
        {
            "step_id": "plan",
            "line": SUCCESS_SUMMARY_LINE,
            "stream": "stdout",
        },
        id="summary_keeps_the_raw_line",
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
        assert_json_payload_matches_published_shape(StepOutputFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", STEP_OUTPUT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[StepOutputEvent, StepOutputEvent]) -> None:
        assert_rich_render_shows_every_view_value(StepOutputFormatter, case.data, case.render_expectations)
