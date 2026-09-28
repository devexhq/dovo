"""Orchestration logic for ``wt history`` list CLI command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.history import History
from worktree.core.history.models import HistoryListResult


def history_root_command(
    context: CliContext,
    limit: int | None = 20,
    status: str | None = None,
    output_format: str = "terminal",
) -> HistoryListResult:
    """Execute history list query and dispatch results via UiDispatcher.

    Args:
        context: CLI context instance.
        limit: Maximum number of execution runs to display.
        status: Filter by run status.
        kind: Filter by blueprint kind.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        HistoryListResult containing listed runs and errors.
    """
    result = History(context.paths, db=context.db.runs).list(
        limit=limit,
        status=status,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result


history_list_command = history_root_command
