"""ComponentFormatter for ConfigUnsetResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.config.mutate import ConfigUnsetResult, ConfigUnsetStatus


class ConfigUnsetFormatter(ComponentFormatter[ConfigUnsetResult]):
    """Formatter for configuration removal results."""

    def to_rich(self, data: ConfigUnsetResult) -> Any:
        """Render configuration removal confirmation or error panel."""
        if data.status == ConfigUnsetStatus.OK:
            return Text.from_markup(f"[bold green]✔  Config unset: {data.key}[/bold green]")

        return build_error_panel(
            "Config Error",
            data.errors,
            "Failed to update configuration.",
            data.fixes,
        )
