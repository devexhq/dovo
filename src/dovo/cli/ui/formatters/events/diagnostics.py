"""ComponentFormatter for DiagnosticsEvent."""

from __future__ import annotations

from rich.console import Group, RenderableType

from dovo.cli.ui.events import DiagnosticsEvent
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import diagnostic_lines


class DiagnosticsFormatter(ComponentFormatter[DiagnosticsEvent]):
    """Formatter for a run's diagnostic-log references."""

    def to_rich(self, data: DiagnosticsEvent) -> RenderableType:
        """Render diagnostic_lines(data.diagnostics), one line per error-level record."""
        return Group(*diagnostic_lines(data.diagnostics))
