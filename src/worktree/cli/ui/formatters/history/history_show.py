"""ComponentFormatter for HistoryShowResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from worktree.cli.ui.formatters.common import build_error_panel
from worktree.cli.ui.formatters.history.common import (
    build_metadata_table,
    build_run_summary,
)
from worktree.cli.ui.formatters.history.history_views import HistoryShowView
from worktree.common.types import ComponentFormatter
from worktree.core.history.models import HistoryShowResult, HistoryShowStatus


def _render_show_not_found(session_id: str | None, fixes: list[str] | None = None) -> Panel:
    """Render error panel when requested session record is not found."""
    session_label = session_id or "unknown"
    fix_list = fixes or ["Run `wt history` to view past sessions"]
    return build_error_panel(
        "Session Not Found",
        default=f"Session '{session_label}' not found.",
        fixes=fix_list,
    )


def _render_show_error(errors: list[str], fixes: list[str] | None = None) -> Panel:
    """Render error panel when session show encounters errors."""
    return build_error_panel("Session Show Failed", errors=errors, fixes=fixes)


def _render_show_error_panel(view: HistoryShowView) -> Panel | None:
    """Check and render error panels for session show operation."""
    if view.status == HistoryShowStatus.NOT_FOUND or (view.run is None and not view.errors):
        return _render_show_not_found(view.session_id, view.fixes)

    if view.errors:
        return _render_show_error(view.errors, view.fixes)

    return None


def _render_show_run(view: HistoryShowView) -> Any:
    """Render detailed session metadata panel, error panel, and step timeline."""
    if view.run is None:
        return Text("")

    renderables: list[Any] = [
        Panel(
            build_metadata_table(view.run),
            title=f"Session Metadata: {view.run.session_id}",
            border_style="blue",
        )
    ]

    if view.run.error_message:
        renderables.append(Panel(view.run.error_message, title="Error Details", border_style="red"))

    if view.log_files:
        renderables.append(
            Panel(Text("\n".join([*view.log_files, "", *view.log_snippet])), title="Session Logs", border_style="cyan")
        )

    return Group(*renderables) if len(renderables) > 1 else renderables[0]


class HistoryShowFormatter(ComponentFormatter[HistoryShowResult, HistoryShowView]):
    """Formatter for history show command results."""

    def transform(self, data: HistoryShowResult) -> HistoryShowView:
        """Derive the presentation-ready view from HistoryShowResult.

        Args:
            data: Domain HistoryShowResult instance.

        Returns:
            HistoryShowView containing the mapped run summary and session log details.
        """
        run_summary = build_run_summary(data.run) if data.run is not None else None
        return HistoryShowView(
            status=data.status,
            session_id=data.session_id,
            run=run_summary,
            log_files=list(data.log_files),
            log_snippet=list(data.log_snippet),
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_rich(self, data: HistoryShowResult) -> Any:
        """Render detailed session metadata panel, error panel, and step timeline."""
        view = self.transform(data)
        error_panel = _render_show_error_panel(view)
        if error_panel is not None:
            return error_panel

        if view.run is not None:
            return _render_show_run(view)

        return Text("")
