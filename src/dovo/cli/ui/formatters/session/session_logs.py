"""ComponentFormatter for LogsShowResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.session.session_views import SessionLogsView
from dovo.common.error_codes import ErrorCode
from dovo.core.sessions import LogsShowResult, SessionLogEvent


def _event_details(event: SessionLogEvent) -> str:
    """Join whichever optional fields an event populates into one plain-text cell."""
    return " ".join(f"{name}={value}" for name, value in event.details().items())


def _panel_title(view: SessionLogsView) -> str:
    """Return the error panel title for the view's error_code; a missing session is 'Logs Not Found' once its id is known."""
    if view.error_code == ErrorCode.SESSION_NOT_FOUND:
        return "Session Not Found" if view.session_id is None else "Logs Not Found"
    if view.error_code == ErrorCode.SESSION_STEP_NOT_FOUND:
        return "Step Not Found"
    if view.error_code == ErrorCode.SESSION_ATTEMPT_NOT_FOUND:
        return "Attempt Not Found"
    if view.error_code == ErrorCode.SESSION_LOGS_UNREADABLE:
        return "Read Failure"
    return "Logs Show Failed"


def _render_error_panel(view: SessionLogsView) -> Panel | None:
    """Render the error panel for a failed view, titled by its error_code, or None when content is renderable."""
    if view.error_code is None:
        return None
    return build_error_panel(_panel_title(view), view.errors, remediations=view.remediations)


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
            **header_fields(data),
            session_id=data.session_id,
            lines=list(data.lines),
            events=list(data.events),
            available_steps=list(data.available_steps),
            available_attempts=list(data.available_attempts),
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
