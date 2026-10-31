"""Tier 2 presentation contract tests for StepSkippedFormatter."""

from __future__ import annotations

import pytest

from dovo.cli.ui.events import StepSkippedEvent
from dovo.cli.ui.formatters.events.guard import StepSkippedFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_PICKED = "steps.answer-questions.outputs.selected_count > 0"

SKIPPED_FALSE = FormatterCase.identity(
    data=StepSkippedEvent(
        idx=5, total=13, step_id="fold", name="Fold the answers into the plan", cause="when_false", expression=_PICKED
    ),
    render_expectations=[f"[5/13] Fold the answers into the plan — skipped (when: {_PICKED} → FALSE)"],
)

SKIPPED_UNRESOLVED = FormatterCase.identity(
    data=StepSkippedEvent(idx=2, total=3, step_id="fold", cause="when_unresolved", expression=_PICKED),
    render_expectations=[f"[2/3] fold — skipped (when: {_PICKED} → UNRESOLVED)"],
)

SKIPPED_CASES = [pytest.param(SKIPPED_FALSE, id="false"), pytest.param(SKIPPED_UNRESOLVED, id="unresolved")]


class StepSkippedFormatterTests:
    """Tier 2 presentation contract tests for StepSkippedFormatter."""

    def test_json_payload_matches_published_shape(self) -> None:
        expected: dict[str, object] = {
            "idx": 5,
            "total": 13,
            "step_id": "fold",
            "name": "Fold the answers into the plan",
            "cause": "when_false",
            "expression": _PICKED,
        }
        assert_json_payload_matches_published_shape(StepSkippedFormatter, SKIPPED_FALSE.data, expected)

    @pytest.mark.parametrize("case", SKIPPED_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[StepSkippedEvent, StepSkippedEvent]) -> None:
        assert_rich_render_shows_every_view_value(StepSkippedFormatter, case.data, case.render_expectations)
