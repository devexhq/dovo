"""Worktree apply command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.worktree import Worktree, WorktreeApplyResult, WorktreeApplyStrategy


def worktree_apply_command(
    context: CliContext,
    worktree_id: str,
    *,
    strategy: WorktreeApplyStrategy = WorktreeApplyStrategy.PATCH,
    allow_dirty: bool = False,
    dry_run: bool = False,
    delete: bool = False,
    message: str | None = None,
    output_format: str = "terminal",
) -> WorktreeApplyResult:
    """Apply worktree changes back to main workspace.

    Args:
        context: CLI context instance.
        worktree_id: Worktree primary key to apply.
        strategy: Apply strategy ('patch' or 'squash').
        allow_dirty: Allow application even if main repository is dirty.
        dry_run: Perform conflict check without mutating workspace.
        delete: Clean up worktree upon successful application.
        message: Optional commit message for squash strategy.
        output_format: Presentation format ("terminal" or "json").
    """
    worktree = Worktree(context.paths, db=context.db.worktrees)
    result = worktree.apply(
        worktree_id=worktree_id,
        strategy=strategy,
        allow_dirty=allow_dirty,
        dry_run=dry_run,
        delete=delete,
        message=message,
    )

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
