"""ComponentFormatter for ConfigLoadResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.serialize import as_json


class ConfigLoadFormatter(ComponentFormatter[ConfigLoadResult]):
    """Formatter for configuration load and validation results."""

    def to_rich(self, data: ConfigLoadResult) -> Any:
        """Render configuration source path header and normalized JSON body, or error panel."""
        if data.ok and data.config is not None:
            payload = f"Config: {data.config_path.as_posix()}\nStatus: valid\n\n{as_json(data.config)}"
            return Text(payload)

        fallback = f"Configuration failed to load ({data.status.value.upper()})."
        return build_error_panel("Config Error", data.errors, fallback, data.fixes)
