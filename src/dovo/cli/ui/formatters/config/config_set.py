"""ComponentFormatter for ConfigSetResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.config.common import format_config_value
from dovo.cli.ui.formatters.config.config_views import ConfigSetView
from dovo.common.types import ComponentFormatter
from dovo.core.config.mutate import ConfigSetResult, ConfigSetStatus


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
            status=data.status,
            config_path=data.config_path,
            key=data.key,
            value=data.value,
            value_str=format_config_value(data.value),
            value_type=type(data.value).__name__,
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_rich(self, data: ConfigSetResult) -> Any:
        """Render configuration update confirmation or error panel."""
        view = self.transform(data)
        if view.status == ConfigSetStatus.OK:
            return Text.from_markup(
                f"[bold green]✔  Config updated: {view.key} = {view.value_str} ({view.value_type})[/bold green]"
            )

        return build_error_panel(
            "Config Error",
            view.errors,
            "Failed to update configuration.",
            view.fixes,
        )
