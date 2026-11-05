"""ComponentFormatter for ConfigValidationResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.config.config_views import ConfigValidationView
from dovo.cli.ui.formatters.header import header_fields
from dovo.common.formatters import format_remediations, format_warning_bullets
from dovo.core.config.validate import ConfigValidationResult


def _format_valid_config(view: ConfigValidationView) -> Text:
    """Format successful configuration validation output with optional warnings and the Fix: block."""
    lines = [
        f"Config: {view.config_path.as_posix()}",
        f"Status: {'valid with warnings' if view.warnings else 'valid'}",
        "",
    ]
    if view.warnings:
        lines.append("Warnings:")
        lines.extend(format_warning_bullets(view.warnings))
        lines.append("")
    if remediations_message := format_remediations(view.remediations):
        lines.append(remediations_message)
        lines.append("")
    lines.append("Config is valid.")
    return Text("\n".join(lines))


def _format_invalid_config(view: ConfigValidationView) -> RenderableType:
    """Format failed configuration validation error panel and warnings, followed by diagnostic_lines(view.diagnostics)."""
    panel = build_error_panel(
        "Config Validation Failed",
        view.errors,
        "Configuration validation failed.",
        remediations=view.remediations,
    )
    if view.warnings:
        warning_block = "Warnings:\n" + "\n".join(format_warning_bullets(view.warnings))
        return with_diagnostics(Group(panel, Text(warning_block)), view.diagnostics)
    return with_diagnostics(panel, view.diagnostics)


class ConfigValidateFormatter(ComponentFormatter[ConfigValidationResult, ConfigValidationView]):
    """Formatter for configuration validation results."""

    event_type: ClassVar[str] = "config.validate"

    def transform(self, data: ConfigValidationResult) -> ConfigValidationView:
        """Derive the presentation-ready view from ConfigValidationResult domain model.

        Args:
            data: Domain ConfigValidationResult object.

        Returns:
            ConfigValidationView carrying the result's fields.
        """
        return ConfigValidationView(
            **header_fields(data),
            config_path=data.config_path,
            raw=data.raw,
            config=data.config,
        )

    def to_rich(self, data: ConfigValidationResult) -> RenderableType:
        """Render the valid report with warning bullets, or the error panel."""
        view = self.transform(data)
        if view.error_code is None:
            return _format_valid_config(view)
        return _format_invalid_config(view)
