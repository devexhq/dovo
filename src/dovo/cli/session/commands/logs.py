"""Orchestration logic for ``dovo session logs`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.cli.ui.events import MessageEvent
from dovo.common.models import OutputFormatOptions
from dovo.core.sessions import LogsShowResult, LogStreamFilter, Session, SessionCollection


def session_logs_command(
    context: CliContext,
    session_id: str | None = None,
    *,
    step: str | None,
    attempt: int | None,
    stream: str,
    tail: int | None,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> LogsShowResult:
    """Execute session log query and dispatch results via UiDispatcher.

    Args:
        context: CLI context instance.
        session_id: Session identifier whose logs to show; the project's latest session when omitted.
        step: Optional step ID filter selecting raw step capture lines.
        attempt: Optional attempt number (defaults to the latest).
        stream: Output stream filter ("stdout", "stderr", or "both").
        tail: Optional number of trailing lines or events to keep.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        LogsShowResult containing session.log events or step log lines.
    """
    stream_filter = LogStreamFilter(stream)
    if session_id is None:
        result = SessionCollection(
            context.paths, db=context.db.sessions, sensitive_variables=context.sensitive_variables
        ).latest_logs(step=step, attempt=attempt, stream=stream_filter, tail=tail)
        if result.session_id is not None and output_format == OutputFormatOptions.TERMINAL:
            ui_dispatcher.dispatch(MessageEvent(message=f"Session: {result.session_id} (latest)"))
    else:
        result = Session(
            context.paths, session_id, db=context.db.sessions, sensitive_variables=context.sensitive_variables
        ).logs(step=step, attempt=attempt, stream=stream_filter, tail=tail)

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
