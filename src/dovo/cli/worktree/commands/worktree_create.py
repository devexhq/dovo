from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.worktree import Worktree
from dovo.core.worktree.models import WorktreeCreateResult


def worktree_create_command(
    context: CliContext,
    name: str | None = None,
    base_ref: str | None = None,
    wip: bool = False,
    output_format: str = "terminal",
) -> WorktreeCreateResult:
    """Create an isolated git worktree.

    Args:
        context: CLI context instance.
        name: Optional human-readable worktree name.
        base_ref: Optional git ref override for worktree creation.
        wip: When True, overlay uncommitted working-tree changes.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Worktree(context.paths, db=context.db.worktrees).create(
        name=name,
        base_ref=base_ref,
        include_wip=wip,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
