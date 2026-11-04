"""ComponentFormatter for HistoryShowResult."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.session.common import (
    build_metadata_table,
    build_session_summary,
    build_step_error_lines,
    build_steps_table,
)
from dovo.cli.ui.formatters.session.session_views import SessionShowView
from dovo.common.error_codes import ErrorCode
from dovo.common.utils import display_path
from dovo.core.sessions import HistoryShowResult


def _panel_title(view: SessionShowView) -> str:
    """Return the error panel title for the view's error_code."""
    if view.error_code == ErrorCode.SESSION_NOT_FOUND:
        return "Session Not Found"
    if view.error_code == ErrorCode.SESSION_LOGS_UNREADABLE:
        return "Read Failure"
    return "Session Show Failed"


def _render_show_error_panel(view: SessionShowView) -> Panel | None:
    """Render the error panel for a failed view, titled by its error_code, or None when the view succeeded."""
    if view.error_code is None:
        return None
    return build_error_panel(_panel_title(view), view.errors, remediations=view.remediations)


def _render_show_body(view: SessionShowView) -> RenderableType:
    """Render the error panel when there is one, else the session run, else nothing."""
    error_panel = _render_show_error_panel(view)
    if error_panel is not None:
        return error_panel

    if view.session is not None:
        return _render_show_run(view)

    return Text("")


STEP_LOGS_HINT = "Use 'dovo logs {session_id} --step <step_id>' for step output."


def _render_show_steps(view: SessionShowView) -> list[RenderableType]:
    """Render the Steps table, its error lines and the logs hint, or the warnings when no steps exist."""
    if not view.steps:
        return [Text(warning, style="dim") for warning in view.warnings]

    return [
        build_steps_table(view.steps),
        *build_step_error_lines(view.steps),
        Text(STEP_LOGS_HINT.format(session_id=view.session_id), style="dim"),
    ]


def _render_show_run(view: SessionShowView) -> RenderableType:
    """Render detailed session metadata panel, error panel, and step timeline."""
    if view.session is None:
        return Text("")

    renderables: list[RenderableType] = [
        Panel(
            build_metadata_table(view.session, worktree_kept=view.worktree_kept, worktree_path=view.worktree_path),
            title=f"Session Metadata: {view.session.session_id}",
            border_style="blue",
        )
    ]

    if view.session.error_message:
        renderables.append(Panel(view.session.error_message, title="Error Details", border_style="red"))

    renderables.extend(_render_show_steps(view))

    if view.log_files:
        renderables.append(
            Panel(Text("\n".join([*view.log_files, "", *view.log_snippet])), title="Session Logs", border_style="cyan")
        )

    return Group(*renderables) if len(renderables) > 1 else renderables[0]


class SessionShowFormatter(ComponentFormatter[HistoryShowResult, SessionShowView]):
    """Formatter for session show command results."""

    def transform(self, data: HistoryShowResult) -> SessionShowView:
        """Derive the presentation-ready view from HistoryShowResult.

        Args:
            data: Domain HistoryShowResult instance.

        Returns:
            SessionShowView containing the mapped session summary and session log details.
        """
        session_summary = build_session_summary(data.session) if data.session is not None else None
        return SessionShowView(
            **header_fields(data),
            session_id=data.session_id,
            session=session_summary,
            log_files=list(data.log_files),
            log_snippet=list(data.log_snippet),
            steps=list(data.steps),
            worktree_kept=data.worktree_kept,
            worktree_path=display_path(data.worktree_path, Path.cwd()) if data.worktree_path is not None else None,
        )

    def to_rich(self, data: HistoryShowResult) -> RenderableType:
        """Render detailed session metadata panel, error panel, and step timeline, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_render_show_body(view), view.diagnostics)
