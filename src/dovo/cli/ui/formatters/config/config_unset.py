"""ComponentFormatter for ConfigUnsetResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.config.config_views import ConfigUnsetView
from dovo.cli.ui.formatters.header import header_fields
from dovo.core.config.mutate import ConfigUnsetResult


class ConfigUnsetFormatter(ComponentFormatter[ConfigUnsetResult, ConfigUnsetView]):
    """Formatter for configuration removal results."""

    event_type: ClassVar[str] = "config.unset"

    def transform(self, data: ConfigUnsetResult) -> ConfigUnsetView:
        """Derive the view: the result header, then the key and the value it held."""
        return ConfigUnsetView(
            **header_fields(data),
            config_path=data.config_path,
            key=data.key,
            existed=data.existed,
            previous_value=data.previous_value,
        )

    def to_rich(self, data: ConfigUnsetResult) -> RenderableType:
        """Render configuration removal confirmation or error panel."""
        view = self.transform(data)
        if view.error_code is None:
            return Text.from_markup(f"[bold green]✔  Config unset: {view.key}[/bold green]")

        panel = build_error_panel(
            "Config Error",
            view.errors,
            "Failed to update configuration.",
            remediations=view.remediations,
        )
        return with_diagnostics(panel, view.diagnostics)
