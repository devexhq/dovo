"""ComponentFormatter for ConfigValidationResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel, render_list_fixes
from dovo.cli.ui.formatters.config.config_views import ConfigValidationView
from dovo.common.formatters import format_warning_bullets
from dovo.common.types import ComponentFormatter
from dovo.core.config.validate import ConfigValidationResult, ConfigValidationStatus


def _derive_status_label(data: ConfigValidationResult) -> str:
    """Derive human-readable status label from validation result."""
    if not data.ok:
        return "invalid"
    return "valid with warnings" if data.warnings else "valid"


def _format_valid_config(view: ConfigValidationView) -> Text:
    """Format successful configuration validation output with optional warnings and fixes."""
    lines = [
        f"Config: {view.config_path.as_posix()}",
        f"Status: {view.status_label}",
        "",
    ]
    if view.warnings:
        lines.append("Warnings:")
        lines.extend(format_warning_bullets(view.warnings))
        lines.append("")
    if fixes_message := render_list_fixes(view.fixes):
        lines.append(fixes_message)
        lines.append("")
    lines.append("Config is valid.")
    return Text("\n".join(lines))


def _format_invalid_config(view: ConfigValidationView) -> Any:
    """Format failed configuration validation error panel and warnings."""
    panel = build_error_panel(
        "Config Validation Failed",
        view.errors,
        "Configuration validation failed.",
        view.fixes,
    )
    if view.warnings:
        warning_block = "Warnings:\n" + "\n".join(format_warning_bullets(view.warnings))
        return Group(panel, Text(warning_block))
    return panel


class ConfigValidateFormatter(ComponentFormatter[ConfigValidationResult, ConfigValidationView]):
    """Formatter for configuration validation results."""

    def transform(self, data: ConfigValidationResult) -> ConfigValidationView:
        """Derive the presentation-ready view from ConfigValidationResult domain model.

        Args:
            data: Domain ConfigValidationResult object.

        Returns:
            ConfigValidationView with derived status_label.
        """
        return ConfigValidationView(
            status=data.status,
            config_path=data.config_path,
            status_label=_derive_status_label(data),
            raw=data.raw,
            config=data.config,
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_rich(self, data: ConfigValidationResult) -> Any:
        """Render status label, warning bullets, or error panel."""
        view = self.transform(data)
        if view.status == ConfigValidationStatus.VALID:
            return _format_valid_config(view)
        return _format_invalid_config(view)
