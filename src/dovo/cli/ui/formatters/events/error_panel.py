"""ComponentFormatter for ErrorPanelEvent."""

from __future__ import annotations

from rich.panel import Panel

from dovo.cli.ui.events import ErrorPanelEvent
from dovo.common.types import ComponentFormatter


class ErrorPanelFormatter(ComponentFormatter[ErrorPanelEvent]):
    """Formatter for error panels."""

    def to_rich(self, data: ErrorPanelEvent) -> Panel:
        """Render error panel with border and title."""
        return Panel(data.message, title=data.title, border_style=data.border_style)
