"""ComponentFormatter for MessageEvent."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text

from dovo.cli.ui.events import MessageEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


class MessageFormatter(ComponentFormatter[MessageEvent]):
    """Formatter for generic message lines."""

    event_type: ClassVar[str] = "message"

    def to_rich(self, data: MessageEvent) -> Text:
        """Render formatted or styled message text."""
        if data.style is not None:
            return Text(data.message, style=data.style)
        return Text.from_markup(data.message)
