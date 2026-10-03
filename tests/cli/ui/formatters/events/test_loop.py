"""Tier 2 presentation contract tests for LoopLifecycleFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import LoopConditionView, LoopLifecycleEvent
from dovo.cli.ui.formatters.events.loop import LoopLifecycleFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    render_rich,
)

LOOP_START = FormatterCase(
    data=LoopLifecycleEvent(
        loop_id="loop_1",
        action="start",
        iteration=None,
        max_iterations=5,
        status=None,
        message=None,
    ),
    view=LoopLifecycleEvent(
        loop_id="loop_1",
        action="start",
        iteration=None,
        max_iterations=5,
        status=None,
        message=None,
    ),
    render_expectations=["loop_1"],
)

ITERATION_START = FormatterCase(
    data=LoopLifecycleEvent(
        loop_id="loop_1",
        action="iteration_start",
        iteration=2,
        max_iterations=5,
        status=None,
        message=None,
    ),
    view=LoopLifecycleEvent(
        loop_id="loop_1",
        action="iteration_start",
        iteration=2,
        max_iterations=5,
        status=None,
        message=None,
    ),
    render_expectations=["loop_1"],
)

CONDITIONS_EVALUATED = FormatterCase(
    data=LoopLifecycleEvent(
        loop_id="loop_1",
        action="conditions_evaluated",
        iteration=None,
        max_iterations=None,
        status=None,
        message="Evaluated 1 condition(s)",
        conditions=[
            LoopConditionView(expression="steps.run-tests.exit_code == 0", passed=False, detail="FALSE (was 127)"),
        ],
        next_iteration=2,
    ),
    view=LoopLifecycleEvent(
        loop_id="loop_1",
        action="conditions_evaluated",
        iteration=None,
        max_iterations=None,
        status=None,
        message="Evaluated 1 condition(s)",
        conditions=[
            LoopConditionView(expression="steps.run-tests.exit_code == 0", passed=False, detail="FALSE (was 127)"),
        ],
        next_iteration=2,
    ),
    render_expectations=["loop_1", "steps.run-tests.exit_code == 0", "FALSE (was 127)"],
)

LOOP_DONE = FormatterCase(
    data=LoopLifecycleEvent(
        loop_id="loop_1",
        action="done",
        iteration=3,
        max_iterations=5,
        status="completed",
        message=None,
    ),
    view=LoopLifecycleEvent(
        loop_id="loop_1",
        action="done",
        iteration=3,
        max_iterations=5,
        status="completed",
        message=None,
    ),
    render_expectations=["loop_1"],
)

LOOP_CASES = [
    pytest.param(LOOP_START, id="loop_start"),
    pytest.param(ITERATION_START, id="iteration_start"),
    pytest.param(CONDITIONS_EVALUATED, id="conditions_evaluated"),
    pytest.param(LOOP_DONE, id="loop_done"),
]

LOOP_PAYLOAD_CASES = [
    pytest.param(
        LOOP_START,
        {
            "loop_id": "loop_1",
            "action": "start",
            "iteration": None,
            "max_iterations": 5,
            "status": None,
            "message": None,
            "conditions": [],
            "next_iteration": None,
        },
        id="loop_start",
    ),
    pytest.param(
        ITERATION_START,
        {
            "loop_id": "loop_1",
            "action": "iteration_start",
            "iteration": 2,
            "max_iterations": 5,
            "status": None,
            "message": None,
            "conditions": [],
            "next_iteration": None,
        },
        id="iteration_start",
    ),
    pytest.param(
        CONDITIONS_EVALUATED,
        {
            "loop_id": "loop_1",
            "action": "conditions_evaluated",
            "iteration": None,
            "max_iterations": None,
            "status": None,
            "message": "Evaluated 1 condition(s)",
            "conditions": [
                {"expression": "steps.run-tests.exit_code == 0", "passed": False, "detail": "FALSE (was 127)"},
            ],
            "next_iteration": 2,
        },
        id="conditions_evaluated",
    ),
    pytest.param(
        LOOP_DONE,
        {
            "loop_id": "loop_1",
            "action": "done",
            "iteration": 3,
            "max_iterations": 5,
            "status": "completed",
            "message": None,
            "conditions": [],
            "next_iteration": None,
        },
        id="loop_done",
    ),
]


class LoopLifecycleFormatterTests:
    """Tier 2 presentation contract tests for LoopLifecycleFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), LOOP_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[LoopLifecycleEvent, LoopLifecycleEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(LoopLifecycleFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", LOOP_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[LoopLifecycleEvent, LoopLifecycleEvent]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(LoopLifecycleFormatter, case.data, case.render_expectations)

    def test_conditions_evaluated_renders_no_literal_backslash(self) -> None:
        """[tier-2/formatter] LoopLifecycleFormatter.to_rich: conditions_evaluated Text contains no literal backslash before the loop id."""
        rendered = render_rich(LoopLifecycleFormatter().to_rich(CONDITIONS_EVALUATED.data))
        assert "\\[" not in rendered
