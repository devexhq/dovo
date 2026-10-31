"""ComponentFormatters for StepSkippedEvent and IfBranchEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import ConditionOutcomeView, IfBranchEvent, StepSkippedEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


def _outcome_word(passed: bool, resolved: bool) -> str:
    """Return TRUE for a passing expression, FALSE for a failing one and UNRESOLVED for one whose operand did not resolve."""
    if passed:
        return "TRUE"
    return "FALSE" if resolved else "UNRESOLVED"


def _condition_text(condition: ConditionOutcomeView) -> str:
    """Return '<expression> → <outcome>' for one evaluated condition."""
    return f"{condition.expression} → {_outcome_word(condition.passed, condition.resolved)}"


class StepSkippedFormatter(ComponentFormatter[StepSkippedEvent]):
    """Formatter for the one line a step or loop skipped by its `when` guard prints."""

    def to_rich(self, data: StepSkippedEvent) -> Text:
        """Render '[idx/total] <label> — skipped (when: <expression> → FALSE|UNRESOLVED)'."""
        outcome = _outcome_word(passed=False, resolved=data.cause == "when_false")
        return Text(
            f"[{data.idx}/{data.total}] {data.name or data.step_id} — skipped (when: {data.expression} → {outcome})",
            style="dim",
        )


class IfBranchFormatter(ComponentFormatter[IfBranchEvent]):
    """Formatter for the one line an `if` block prints when it chooses its branch."""

    def to_rich(self, data: IfBranchEvent) -> Text:
        """Render '[idx/total] <label> — <branch> (if: <expression> → <outcome>; ...)'."""
        conditions = "; ".join(_condition_text(condition) for condition in data.conditions)
        return Text(f"[{data.idx}/{data.total}] {data.name or data.block_id} — {data.branch} (if: {conditions})")
