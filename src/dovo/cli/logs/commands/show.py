"""Orchestration logic for ``dovo logs`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sessions import LogsShowResult, LogStreamFilter, Session


def logs_show_command(
    context: CliContext,
    session_id: str,
    *,
    step: str | None,
    attempt: int | None,
    stream: str,
    tail: int | None,
    output_format: str = "terminal",
) -> LogsShowResult:
    """Execute session log query and dispatch results via UiDispatcher.

    Args:
        context: CLI context instance.
        session_id: Session identifier whose logs to show.
        step: Optional step ID filter selecting raw step capture lines.
        attempt: Optional attempt number (defaults to the latest).
        stream: Output stream filter ("stdout", "stderr", or "both").
        tail: Optional number of trailing lines or events to keep.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        LogsShowResult containing session.log events or step log lines.
    """
    result = Session(
        context.paths, session_id, db=context.db.sessions, sensitive_variables=context.sensitive_variables
    ).logs(step=step, attempt=attempt, stream=LogStreamFilter(stream), tail=tail)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
