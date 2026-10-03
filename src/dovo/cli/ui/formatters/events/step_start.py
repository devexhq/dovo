"""ComponentFormatter for StepStartEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import StepStartEvent
from dovo.common.types import ComponentFormatter


class StepStartFormatter(ComponentFormatter[StepStartEvent]):
    """Formatter for step start notices."""

    def to_rich(self, data: StepStartEvent) -> Text:
        """Render step start progress line."""
        step_label = data.name or data.step_id
        cmd_info = f" (command: {data.command})" if data.command else ""
        return Text.from_markup(f"[STEP {data.idx}/{data.total}] Executing {step_label}{cmd_info}...")
