"""ComponentFormatter for ConfigLoadResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.config.config_views import ConfigLoadView
from dovo.cli.ui.formatters.header import header_fields
from dovo.core.config.loader import ConfigLoadResult
from dovo.core.config.serialize import as_json


class ConfigLoadFormatter(ComponentFormatter[ConfigLoadResult, ConfigLoadView]):
    """Formatter for configuration load and validation results."""

    event_type: ClassVar[str] = "config.show"

    def transform(self, data: ConfigLoadResult) -> ConfigLoadView:
        """Derive the view: the result header, then the raw and typed config."""
        return ConfigLoadView(**header_fields(data), config_path=data.config_path, raw=data.raw, config=data.config)

    def to_rich(self, data: ConfigLoadResult) -> RenderableType:
        """Render configuration source path header and normalized JSON body, or error panel."""
        view = self.transform(data)
        if view.ok and view.config is not None:
            payload = f"Config: {view.config_path.as_posix()}\nStatus: valid\n\n{as_json(view.config)}"
            return Text(payload)

        fallback = f"Configuration failed to load ({view.error_code})."
        panel = build_error_panel("Config Error", view.errors, fallback, remediations=view.remediations)
        return with_diagnostics(panel, view.diagnostics)
