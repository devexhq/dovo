"""ComponentFormatter for StepDoneEvent."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.events import StepDoneEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


class StepDoneFormatter(ComponentFormatter[StepDoneEvent]):
    """Formatter for step completion and failure notices."""

    def to_rich(self, data: StepDoneEvent) -> Group:
        """Render step completed or failed line followed by a blank line."""
        step_label = data.step_id
        if data.ok:
            line = Text.from_markup(f"[bold green][STEP {data.idx}/{data.total}] {step_label} COMPLETED[/]")
        else:
            msg = data.error_message or f"exit code {data.exit_code}"
            line = Text.from_markup(f"[bold red][STEP {data.idx}/{data.total}] {step_label} FAILED[/]: {msg}")

        return Group(line, Text(""))
