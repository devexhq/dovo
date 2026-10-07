"""Orchestration logic for ``dovo history show`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sessions import HistoryShowResult, Session


def history_show_command(
    context: CliContext,
    session_id: str,
    logs: bool = False,
    output_format: str = "terminal",
) -> HistoryShowResult:
    """Execute session show query and dispatch results via UiDispatcher.

    Args:
        context: CLI context instance.
        session_id: Session identifier to inspect.
        logs: Whether to include log file paths and a recent session.log snippet.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        HistoryShowResult containing session details and errors.
    """
    result = Session(
        context.paths, session_id, db=context.db.sessions, sensitive_variables=context.sensitive_variables
    ).details(include_logs=logs)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
