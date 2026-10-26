"""Common formatting utilities and styles across formatters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from dovo.common.diagnostic_log import diagnostic_hint_line
from dovo.common.formatters import format_remediations

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType

    from dovo.common.diagnostic_log import DiagnosticRecord
    from dovo.common.models import Remediation

DEFAULT_PANEL_STYLE = "blue"
ERROR_PANEL_STYLE = "red"
WARNING_STYLE = "yellow"
SUCCESS_STYLE = "green"


def render_list_errors(
    errors: Sequence[str] | None = None,
    default: str = "",
    *,
    separator: str = "\n\n",
) -> str:
    """Render a sequence of errors joined by separator, or a default message.

    Args:
        errors: Sequence of error strings to render.
        default: Fallback string if errors is empty or None.
        separator: String separator between error messages. Defaults to double newline.

    Returns:
        Formatted error string.
    """
    if errors:
        return separator.join(errors)
    return default


def build_error_panel(
    title: str,
    errors: Sequence[str] | None = None,
    default: str = "",
    remediations: Sequence[Remediation] = (),
    *,
    border_style: str = ERROR_PANEL_STYLE,
    fit: bool = False,
) -> Panel:
    """Build a standardized Rich error panel combining formatted errors and the remediation Fix: block.

    Args:
        title: Panel title header.
        errors: Sequence of error strings.
        default: Fallback error message if errors is empty or None.
        remediations: Remediations rendered as the shared Fix: block.
        border_style: Border style color. Defaults to ERROR_PANEL_STYLE ("red").
        fit: Whether to use Panel.fit instead of Panel. Defaults to False.

    Returns:
        Configured Rich Panel.
    """
    error_msg = render_list_errors(errors, default=default)
    remediations_msg = format_remediations(remediations)
    message = f"{error_msg}\n{remediations_msg}" if remediations_msg else error_msg
    panel_cls = Panel.fit if fit else Panel
    return panel_cls(message, title=title, border_style=border_style)


def diagnostic_lines(diagnostics: Sequence[DiagnosticRecord]) -> list[Text]:
    """Return one plain 'Details logged: <log_path> (event <event_id>)' Text per error-level record, in order."""
    return [Text(line) for record in diagnostics if (line := diagnostic_hint_line(record)) is not None]


def with_diagnostics(renderable: RenderableType, diagnostics: Sequence[DiagnosticRecord]) -> RenderableType:
    """Return renderable followed by diagnostic_lines(diagnostics), or renderable unchanged when there are none."""
    lines = diagnostic_lines(diagnostics)
    if not lines:
        return renderable

    return Group(renderable, *lines)


__all__ = [
    "DEFAULT_PANEL_STYLE",
    "ERROR_PANEL_STYLE",
    "SUCCESS_STYLE",
    "WARNING_STYLE",
    "build_error_panel",
    "diagnostic_lines",
    "render_list_errors",
    "with_diagnostics",
]
