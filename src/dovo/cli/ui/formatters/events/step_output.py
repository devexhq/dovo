"""ComponentFormatter for StepOutputEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import StepOutputEvent
from dovo.common.types import ComponentFormatter


class StepOutputFormatter(ComponentFormatter[StepOutputEvent]):
    """Formatter for live step output lines."""

    def to_rich(self, data: StepOutputEvent) -> Text:
        """Render raw output text."""
        return Text(data.line)
