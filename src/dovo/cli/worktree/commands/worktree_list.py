"""Worktree list command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.worktree import (
    Worktree,
)
from dovo.core.worktree.models import WorktreeListResult


def worktree_list_command(
    context: CliContext,
    status: str | None = None,
    output_format: str = "terminal",
) -> WorktreeListResult:
    """List tracked worktrees with lifecycle status.

    Read-only aside from reconciling stale ``active`` rows whose worktree
    directory was removed out-of-band.

    Args:
        context: CLI context instance.
        status: Optional status filter validated by Typer at the CLI layer.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Worktree(context.paths, db=context.db.worktrees).list(status=status)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
