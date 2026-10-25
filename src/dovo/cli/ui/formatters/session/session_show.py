"""ComponentFormatter for HistoryShowResult."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.session.common import (
    build_metadata_table,
    build_session_summary,
    build_step_error_lines,
    build_steps_table,
)
from dovo.cli.ui.formatters.session.session_views import SessionShowView
from dovo.common.models import Remediation
from dovo.common.utils import display_path
from dovo.core.sessions import HistoryShowResult, HistoryShowStatus


def _render_show_not_found(session_id: str | None, remediations: list[Remediation]) -> Panel:
    """Render error panel when requested session record is not found."""
    default = f"Session '{session_id}' not found." if session_id else "No sessions found."
    return build_error_panel("Session Not Found", default=default, remediations=remediations)


def _render_show_error(errors: list[str], remediations: list[Remediation]) -> Panel:
    """Render error panel when session show encounters errors."""
    return build_error_panel("Session Show Failed", errors=errors, remediations=remediations)


def _render_show_error_panel(view: SessionShowView) -> Panel | None:
    """Check and render error panels for session show operation."""
    if view.status == HistoryShowStatus.NOT_FOUND or (view.session is None and not view.errors):
        return _render_show_not_found(view.session_id, view.remediations)

    if view.errors:
        return _render_show_error(view.errors, view.remediations)

    return None


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
            status=data.status,
            session_id=data.session_id,
            session=session_summary,
            log_files=list(data.log_files),
            log_snippet=list(data.log_snippet),
            steps=list(data.steps),
            worktree_kept=data.worktree_kept,
            worktree_path=display_path(data.worktree_path, Path.cwd()) if data.worktree_path is not None else None,
            errors=list(data.errors),
            warnings=list(data.warnings),
            remediations=list(data.remediations),
        )

    def to_rich(self, data: HistoryShowResult) -> RenderableType:
        """Render detailed session metadata panel, error panel, and step timeline."""
        view = self.transform(data)
        error_panel = _render_show_error_panel(view)
        if error_panel is not None:
            return error_panel

        if view.session is not None:
            return _render_show_run(view)

        return Text("")
