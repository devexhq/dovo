"""Orchestration logic for ``dovo history`` list CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sessions import HistoryListResult, SessionCollection


def history_root_command(
    context: CliContext,
    limit: int | None = 20,
    status: str | None = None,
    output_format: str = "terminal",
) -> HistoryListResult:
    """Execute history list query and dispatch results via UiDispatcher.

    Args:
        context: CLI context instance.
        limit: Maximum number of sessions to display.
        status: Filter by session status.
        kind: Filter by blueprint kind.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        HistoryListResult containing listed sessions and errors.
    """
    result = SessionCollection(
        context.paths, db=context.db.sessions, sensitive_variables=context.sensitive_variables
    ).list(
        limit=limit,
        status=status,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result


history_list_command = history_root_command
