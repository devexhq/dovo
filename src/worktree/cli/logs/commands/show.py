"""Orchestration logic for ``wt logs`` CLI command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.logs import Logs, LogsShowResult, LogStreamFilter


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
        LogsShowResult containing run.log events or step log lines.
    """
    result = Logs(context.paths, db=context.db.runs).show(
        session_id, step=step, attempt=attempt, stream=LogStreamFilter(stream), tail=tail
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
