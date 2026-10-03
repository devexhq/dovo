"""ComponentFormatter for WarningEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import WarningEvent
from dovo.common.types import ComponentFormatter


class WarningFormatter(ComponentFormatter[WarningEvent]):
    """Formatter for warning notices."""

    def to_rich(self, data: WarningEvent) -> Text:
        """Render warning notice in yellow."""
        return Text.from_markup(f"[yellow]Warning:[/] {data.message}")
