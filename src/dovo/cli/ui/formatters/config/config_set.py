"""ComponentFormatter for ConfigSetResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter, result_header
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.config.common import format_config_value
from dovo.cli.ui.formatters.config.config_views import ConfigSetView
from dovo.core.config.mutate import ConfigSetResult


class ConfigSetFormatter(ComponentFormatter[ConfigSetResult, ConfigSetView]):
    """Formatter for configuration mutation results."""

    def transform(self, data: ConfigSetResult) -> ConfigSetView:
        """Derive the presentation-ready view from ConfigSetResult domain model.

        Args:
            data: Domain ConfigSetResult object.

        Returns:
            ConfigSetView with formatted value string and type name.
        """
        return ConfigSetView(
            **result_header(data),
            config_path=data.config_path,
            key=data.key,
            value=data.value,
            value_str=format_config_value(data.value),
            value_type=type(data.value).__name__,
        )

    def to_rich(self, data: ConfigSetResult) -> RenderableType:
        """Render configuration update confirmation or error panel."""
        view = self.transform(data)
        if view.error_code is None:
            return Text.from_markup(
                f"[bold green]✔  Config updated: {view.key} = {view.value_str} ({view.value_type})[/bold green]"
            )

        panel = build_error_panel(
            "Config Error",
            view.errors,
            "Failed to update configuration.",
            remediations=view.remediations,
        )
        return with_diagnostics(panel, view.diagnostics)
