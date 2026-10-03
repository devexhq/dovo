"""Worktree show command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.worktree import (
    Worktree,
)
from dovo.core.worktree.models import WorktreeShowResult


def worktree_show_command(
    context: CliContext,
    worktree_id: str,
    output_format: str = "terminal",
) -> WorktreeShowResult:
    """Show detail for one tracked worktree.

    Read-only aside from reconciling a stale ``active`` row whose worktree
    directory was removed out-of-band.

    Args:
        context: CLI context instance.
        worktree_id: Worktree primary key to show.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Worktree(context.paths, db=context.db.worktrees).show(worktree_id)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
