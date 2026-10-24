"""Common formatting utilities and styles across formatters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from rich.panel import Panel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

    from dovo.cli.ui.formatters.base import ComponentFormatter

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


def render_list_fixes(
    fixes: Sequence[str] | None = None,
    *,
    header: str = "Fix:",
    bullet: str = "- ",
) -> str:
    """Render a sequence of remediation fixes with a header and bullet points.

    Args:
        fixes: Sequence of fix remediation strings.
        header: Header text before the fixes list. Defaults to "Fix:".
        bullet: Bullet prefix for each fix item. Defaults to "- ".

    Returns:
        Formatted fix block string, or empty string if fixes is empty or None.
    """
    if not fixes:
        return ""
    bullet_lines = "\n".join(f"{bullet}{fix}" for fix in fixes)
    return f"{header}\n{bullet_lines}"


def build_error_panel(
    title: str,
    errors: Sequence[str] | None = None,
    default: str = "",
    fixes: Sequence[str] | None = None,
    *,
    border_style: str = ERROR_PANEL_STYLE,
    fit: bool = False,
) -> Panel:
    """Build a standardized Rich error panel combining formatted errors and remediation fixes.

    Args:
        title: Panel title header.
        errors: Sequence of error strings.
        default: Fallback error message if errors is empty or None.
        fixes: Optional sequence of remediation fix strings.
        border_style: Border style color. Defaults to ERROR_PANEL_STYLE ("red").
        fit: Whether to use Panel.fit instead of Panel. Defaults to False.

    Returns:
        Configured Rich Panel.
    """
    error_msg = render_list_errors(errors, default=default)
    fixes_msg = render_list_fixes(fixes)
    message = f"{error_msg}\n{fixes_msg}" if fixes_msg else error_msg
    panel_cls = Panel.fit if fit else Panel
    return panel_cls(message, title=title, border_style=border_style)


class DispatcherProtocol(Protocol):
    """Protocol for dispatcher registration entrypoint."""

    def register(
        self,
        model_cls: type[BaseModel],
        formatter: ComponentFormatter[Any],
    ) -> object:
        """Register a formatter instance for a specific result or event type."""
        ...


__all__ = [
    "DEFAULT_PANEL_STYLE",
    "ERROR_PANEL_STYLE",
    "SUCCESS_STYLE",
    "WARNING_STYLE",
    "DispatcherProtocol",
    "build_error_panel",
    "render_list_errors",
    "render_list_fixes",
]
