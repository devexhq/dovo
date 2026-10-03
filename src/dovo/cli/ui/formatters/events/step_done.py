"""ComponentFormatter for StepDoneEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import StepDoneEvent
from dovo.common.types import ComponentFormatter


class StepDoneFormatter(ComponentFormatter[StepDoneEvent]):
    """Formatter for step completion and failure notices."""

    def to_rich(self, data: StepDoneEvent) -> Text:
        """Render step completed or failed line."""
        step_label = data.step_id
        if data.ok:
            return Text.from_markup(f"[bold green][STEP {data.idx}/{data.total}] {step_label} COMPLETED[/]")
        msg = data.error_message or f"exit code {data.exit_code}"
        return Text.from_markup(f"[bold red][STEP {data.idx}/{data.total}] {step_label} FAILED[/]: {msg}")
