"""Worktree diff command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.worktree import Worktree
from dovo.core.worktree.models import WorktreeDiffResult


def worktree_diff_command(
    context: CliContext,
    worktree_id: str,
    *,
    stat: bool = False,
    output_format: str = "terminal",
) -> WorktreeDiffResult:
    """Inspect unified diff or file summary statistics for a worktree.

    Args:
        context: CLI context instance.
        worktree_id: Worktree primary key to diff.
        stat: When True, show diffstat summary instead of full unified diff.
        output_format: Presentation format ("terminal" or "json").
    """
    worktree = Worktree(context.paths, db=context.db.worktrees)
    result = worktree.diff(worktree_id, stat=stat)

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
