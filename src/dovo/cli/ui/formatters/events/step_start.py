"""ComponentFormatter for StepStartEvent."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.events import StepStartEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


class StepStartFormatter(ComponentFormatter[StepStartEvent]):
    """Formatter for step start notices."""

    def to_rich(self, data: StepStartEvent) -> Group:
        """Render step start progress line after a blank line."""
        step_label = data.name or data.step_id
        cmd_info = f" (command: {data.command})" if data.command else ""
        return Group(
            Text(""),
            Text.assemble(
                (f"[STEP {data.idx}/{data.total}] Executing", "bold cyan"),
                f" {step_label}{cmd_info}...",
            ),
        )
