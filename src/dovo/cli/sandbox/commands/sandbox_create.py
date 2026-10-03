from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sandbox import Sandbox
from dovo.core.sandbox.models import SandboxCreateResult


def sandbox_create_command(
    context: CliContext,
    name: str | None = None,
    base_ref: str | None = None,
    wip: bool = False,
    output_format: str = "terminal",
) -> SandboxCreateResult:
    """Create an isolated git worktree sandbox.

    Args:
        context: CLI context instance.
        name: Optional human-readable sandbox name.
        base_ref: Optional git ref override for worktree creation.
        wip: When True, overlay uncommitted working-tree changes.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Sandbox(context.paths, db=context.db.sandboxes).create(
        name=name,
        base_ref=base_ref,
        include_wip=wip,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
