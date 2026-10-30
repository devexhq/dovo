"""ComponentFormatter for ConfigUnsetResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.core.config.mutate import ConfigUnsetResult, ConfigUnsetStatus


class ConfigUnsetFormatter(ComponentFormatter[ConfigUnsetResult]):
    """Formatter for configuration removal results."""

    def to_rich(self, data: ConfigUnsetResult) -> RenderableType:
        """Render configuration removal confirmation or error panel."""
        if data.status == ConfigUnsetStatus.OK:
            return Text.from_markup(f"[bold green]✔  Config unset: {data.key}[/bold green]")

        panel = build_error_panel(
            "Config Error",
            data.errors,
            "Failed to update configuration.",
            remediations=data.remediations,
        )
        return with_diagnostics(panel, data.diagnostics)
