"""ComponentFormatter for RunSuccessEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import RunSuccessEvent
from dovo.common.types import ComponentFormatter


class RunSuccessFormatter(ComponentFormatter[RunSuccessEvent]):
    """Formatter for blueprint run completion summaries."""

    def to_rich(self, data: RunSuccessEvent) -> Text:
        """Render green success summary line."""
        return Text.from_markup(
            f"[bold green]Blueprint Run Completed:[/] {data.blueprint_name} "
            f"(session: {data.session_id}, status: {data.status.value})"
        )
