"""Tier 2 presentation contract tests for StepSkippedFormatter and IfBranchFormatter."""

from __future__ import annotations

import pytest

from dovo.cli.ui.events import ConditionOutcomeView, IfBranchEvent, StepSkippedEvent
from dovo.cli.ui.formatters.events.guard import IfBranchFormatter, StepSkippedFormatter
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

_VERDICT = 'steps.check.outputs.verdict == "not_ready"'

BRANCH_ELSE = FormatterCase.identity(
    data=IfBranchEvent(
        idx=3,
        total=4,
        block_id="route",
        branch="else",
        conditions=[ConditionOutcomeView(expression=_VERDICT, passed=False, resolved=True)],
    ),
    render_expectations=[f"[3/4] route — else (if: {_VERDICT} → FALSE)"],
)

BRANCH_THEN = FormatterCase.identity(
    data=IfBranchEvent(
        idx=1,
        total=2,
        block_id="route",
        name="Route the plan",
        branch="then",
        conditions=[
            ConditionOutcomeView(expression=_VERDICT, passed=True, resolved=True),
            ConditionOutcomeView(expression="steps.check.exit_code == 0", passed=True, resolved=True),
        ],
    ),
    render_expectations=[f"[1/2] Route the plan — then (if: {_VERDICT} → TRUE; steps.check.exit_code == 0 → TRUE)"],
)

BRANCH_UNRESOLVED = FormatterCase.identity(
    data=IfBranchEvent(
        idx=2,
        total=2,
        block_id="route",
        branch="none",
        conditions=[ConditionOutcomeView(expression=_VERDICT, passed=False, resolved=False)],
    ),
    render_expectations=[f"[2/2] route — none (if: {_VERDICT} → UNRESOLVED)"],
)

BRANCH_CASES = [
    pytest.param(BRANCH_ELSE, id="else_false"),
    pytest.param(BRANCH_THEN, id="then_two_true"),
    pytest.param(BRANCH_UNRESOLVED, id="none_unresolved"),
]

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


class IfBranchFormatterTests:
    """Tier 2 presentation contract tests for IfBranchFormatter."""

    def test_json_payload_matches_published_shape(self) -> None:
        expected: dict[str, object] = {
            "idx": 3,
            "total": 4,
            "block_id": "route",
            "name": None,
            "branch": "else",
            "conditions": [{"expression": _VERDICT, "passed": False, "resolved": True}],
        }
        assert_json_payload_matches_published_shape(IfBranchFormatter, BRANCH_ELSE.data, expected)

    @pytest.mark.parametrize("case", BRANCH_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[IfBranchEvent, IfBranchEvent]) -> None:
        assert_rich_render_shows_every_view_value(IfBranchFormatter, case.data, case.render_expectations)
