"""Orchestration logic for ``dovo session show`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.cli.ui.events import MessageEvent
from dovo.core.sessions import HistoryShowResult, Session, SessionCollection


def session_show_command(
    context: CliContext,
    session_id: str | None = None,
    logs: bool = False,
    output_format: str = "terminal",
) -> HistoryShowResult:
    """Execute session show query and dispatch results via UiDispatcher.

    Args:
        context: CLI context instance.
        session_id: Session identifier to inspect; the project's latest session when omitted.
        logs: Whether to include log file paths and a recent session.log snippet.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        HistoryShowResult containing session details and errors.
    """
    if session_id is None:
        result = SessionCollection(
            context.paths, db=context.db.sessions, sensitive_variables=context.sensitive_variables
        ).latest_details(include_logs=logs)
        if result.session_id is not None and output_format == "terminal":
            ui_dispatcher.dispatch(MessageEvent(message=f"Session: {result.session_id} (latest)"))
    else:
        result = Session(
            context.paths, session_id, db=context.db.sessions, sensitive_variables=context.sensitive_variables
        ).details(include_logs=logs)

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
