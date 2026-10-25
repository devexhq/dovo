"""ComponentFormatter for HistoryListResult."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.session.common import (
    build_session_summary,
    build_session_table,
)
from dovo.cli.ui.formatters.session.session_views import SessionListView
from dovo.core.sessions import HistoryListResult


def _render_list_runs(view: SessionListView) -> RenderableType:
    """Render execution history sessions table or empty text alongside optional warnings."""
    content: RenderableType = (
        build_session_table(view.sessions) if view.sessions else Text("No execution history found.")
    )
    if not view.warnings:
        return content

    renderables: list[RenderableType] = []
    for warning in view.warnings:
        renderables.append(Text.from_markup(f"[yellow]Warning:[/] {warning}"))
    renderables.append(content)
    return Group(*renderables)


class SessionListFormatter(ComponentFormatter[HistoryListResult, SessionListView]):
    """Formatter for session list command results."""

    def transform(self, data: HistoryListResult) -> SessionListView:
        """Derive the presentation-ready view from HistoryListResult.

        Args:
            data: Domain HistoryListResult instance.

        Returns:
            SessionListView containing mapped session summaries with elapsed durations.
        """
        sessions = [build_session_summary(session) for session in data.sessions]
        return SessionListView(
            status=data.status,
            sessions=sessions,
            total_sessions=len(sessions),
            errors=list(data.errors),
            warnings=list(data.warnings),
            remediations=list(data.remediations),
        )

    def to_rich(self, data: HistoryListResult) -> RenderableType:
        """Render execution history table, warnings, or empty state."""
        view = self.transform(data)
        if view.errors:
            return build_error_panel("Session List Failed", view.errors, remediations=view.remediations)

        return _render_list_runs(view)
