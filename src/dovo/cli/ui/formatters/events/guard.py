"""ComponentFormatter for StepSkippedEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import StepSkippedEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


def _outcome_word(passed: bool, resolved: bool) -> str:
    """Return TRUE for a passing expression, FALSE for a failing one and UNRESOLVED for one whose operand did not resolve."""
    if passed:
        return "TRUE"
    return "FALSE" if resolved else "UNRESOLVED"


class StepSkippedFormatter(ComponentFormatter[StepSkippedEvent]):
    """Formatter for the one line a step or loop skipped by its `when` guard prints."""

    def to_rich(self, data: StepSkippedEvent) -> Text:
        """Render '[idx/total] <label> — skipped (when: <expression> → FALSE|UNRESOLVED)'."""
        outcome = _outcome_word(passed=False, resolved=data.cause == "when_false")
        return Text(
            f"[{data.idx}/{data.total}] {data.name or data.step_id} — skipped (when: {data.expression} → {outcome})",
            style="dim",
        )
