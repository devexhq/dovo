"""Worktree delete command handler."""

from __future__ import annotations

import typer

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.db import WorktreeRecord
from dovo.core.worktree import Worktree, WorktreeDeleteResult


def _confirm_delete(row: WorktreeRecord) -> bool:
    """Ask to delete row's worktree and branch; True when confirmed, False when declined or aborted."""
    try:
        return typer.confirm(
            f"Delete worktree '{row.id}' (branch {row.branch_name}, path {row.worktree_path})?\n"
            "This removes the git worktree and branch.",
            default=False,
        )
    except typer.Abort:
        return False


def worktree_delete_command(
    context: CliContext,
    worktree_id: str,
    force: bool = False,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> WorktreeDeleteResult:
    """Delete a tracked worktree and branch, confirming first unless force.

    Args:
        context: CLI context instance.
        worktree_id: Worktree primary key to delete.
        force: When True, skip the confirmation prompt.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Worktree(
        context.paths, git=context.git, clock=context.clock, new_id=context.new_id, db=context.db.worktrees
    ).delete(worktree_id, confirm=None if force else _confirm_delete)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
