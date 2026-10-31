"""ComponentFormatter for BlueprintInvalidEvent."""

from __future__ import annotations

from rich.panel import Panel

from dovo.cli.ui.events import BlueprintInvalidEvent
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel


class BlueprintInvalidFormatter(ComponentFormatter[BlueprintInvalidEvent]):
    """Formatter for a blueprint that failed validation before any step ran."""

    def to_rich(self, data: BlueprintInvalidEvent) -> Panel:
        """Render the Blueprint Invalid panel: the headline, one line per error, then the Fix: block."""
        lines = [f"Blueprint '{data.blueprint}' failed validation; no steps ran.", *(f"- {e}" for e in data.errors)]
        return build_error_panel("Blueprint Invalid", ["\n".join(lines)], remediations=data.remediations)
