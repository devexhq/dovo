"""ComponentFormatter for HistoryListResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.history.common import (
    build_history_table,
    build_session_summary,
)
from dovo.cli.ui.formatters.history.history_views import HistoryListView
from dovo.common.types import ComponentFormatter
from dovo.core.sessions import HistoryListResult


def _render_list_runs(view: HistoryListView) -> Any:
    """Render execution history sessions table or empty text alongside optional warnings."""
    content: Any = build_history_table(view.sessions) if view.sessions else Text("No execution history found.")
    if not view.warnings:
        return content

    renderables: list[Any] = []
    for warning in view.warnings:
        renderables.append(Text.from_markup(f"[yellow]Warning:[/] {warning}"))
    renderables.append(content)
    return Group(*renderables)


class HistoryListFormatter(ComponentFormatter[HistoryListResult, HistoryListView]):
    """Formatter for history list command results."""

    def transform(self, data: HistoryListResult) -> HistoryListView:
        """Derive the presentation-ready view from HistoryListResult.

        Args:
            data: Domain HistoryListResult instance.

        Returns:
            HistoryListView containing mapped session summaries with elapsed durations.
        """
        sessions = [build_session_summary(session) for session in data.sessions]
        return HistoryListView(
            status=data.status,
            sessions=sessions,
            total_sessions=len(sessions),
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_rich(self, data: HistoryListResult) -> Any:
        """Render execution history table, warnings, or empty state."""
        view = self.transform(data)
        if view.errors:
            return build_error_panel("History List Failed", view.errors, fixes=view.fixes)

        return _render_list_runs(view)
