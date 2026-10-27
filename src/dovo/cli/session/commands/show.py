"""Orchestration logic for ``dovo session show`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.cli.ui.events import MessageEvent
from dovo.common.models import OutputFormatOptions
from dovo.core.sessions import HistoryShowResult
from dovo.engine.services import SessionShowService


def session_show_command(
    context: CliContext,
    session_id: str | None = None,
    logs: bool = False,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
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
    result = SessionShowService(
        paths=context.paths,
        sessions_db=context.db.sessions,
        sensitive_variables=context.sensitive_variables,
        session_id=session_id,
        include_logs=logs,
    ).execute()

    if session_id is None and result.session_id is not None and output_format == OutputFormatOptions.TERMINAL:
        ui_dispatcher.dispatch(MessageEvent(message=f"Session: {result.session_id} (latest)"))

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
