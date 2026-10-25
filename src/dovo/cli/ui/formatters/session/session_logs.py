"""ComponentFormatter for LogsShowResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.session.session_views import SessionLogsView
from dovo.core.sessions import LogsShowResult, LogsShowStatus, SessionLogEvent


def _event_details(event: SessionLogEvent) -> str:
    """Join whichever optional fields an event populates into one plain-text cell."""
    return " ".join(f"{name}={value}" for name, value in event.details().items())


def _render_error_panel(view: SessionLogsView) -> Panel | None:
    """Render the not-found or error panel for a non-OK view, or None when content is renderable."""
    if view.status == LogsShowStatus.SESSION_NOT_FOUND and view.session_id is None:
        return build_error_panel(
            "Session Not Found",
            default="No sessions found.",
            remediations=view.remediations,
        )
    if view.status == LogsShowStatus.SESSION_NOT_FOUND:
        return build_error_panel(
            "Logs Not Found",
            default=f"No logs found for session '{view.session_id}'",
            remediations=view.remediations,
        )
    if view.status == LogsShowStatus.STEP_NOT_FOUND:
        return build_error_panel(
            "Step Not Found",
            default=f"Available steps: {', '.join(view.available_steps) or 'none'}",
            remediations=view.remediations,
        )
    if view.status == LogsShowStatus.ATTEMPT_NOT_FOUND:
        return build_error_panel(
            "Attempt Not Found",
            default=f"Available attempts: {', '.join(str(a) for a in view.available_attempts)}",
            remediations=view.remediations,
        )
    if view.errors:
        return build_error_panel("Logs Show Failed", errors=view.errors, remediations=view.remediations)
    return None


def _build_events_table(events: list[SessionLogEvent]) -> Table:
    """Lay out session.log events as one table row per event."""
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Time", no_wrap=True)
    table.add_column("Event", no_wrap=True)
    table.add_column("Details")
    for event in events:
        table.add_row(event.ts, event.event.value, _event_details(event))
    return table


class SessionLogsFormatter(ComponentFormatter[LogsShowResult, SessionLogsView]):
    """Formatter for session logs command results."""

    def transform(self, data: LogsShowResult) -> SessionLogsView:
        """Derive the presentation-ready view from LogsShowResult."""
        return SessionLogsView(
            status=data.status,
            session_id=data.session_id,
            lines=list(data.lines),
            events=list(data.events),
            available_steps=list(data.available_steps),
            available_attempts=list(data.available_attempts),
            errors=list(data.errors),
            warnings=list(data.warnings),
            remediations=list(data.remediations),
        )

    def to_rich(self, data: LogsShowResult) -> RenderableType:
        """Render session.log events as a table, or step log lines verbatim."""
        view = self.transform(data)
        error_panel = _render_error_panel(view)
        if error_panel is not None:
            return error_panel

        if view.events:
            return _build_events_table(view.events)

        return Text("\n".join(view.lines))
