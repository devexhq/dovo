from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.core.db import WorktreeStatus
from dovo.core.worktree.models import WorktreeApplyStrategy

from .commands.worktree_apply import worktree_apply_command
from .commands.worktree_create import worktree_create_command
from .commands.worktree_delete import worktree_delete_command
from .commands.worktree_diff import worktree_diff_command
from .commands.worktree_list import worktree_list_command
from .commands.worktree_prune import worktree_prune_command
from .commands.worktree_show import worktree_show_command

worktree_app = typer.Typer(
    name="worktree",
    help="Inspect and manage git worktrees.",
)


@worktree_app.command("create")
def worktree_create(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None,
        "--name",
        help="Optional human-readable name for the worktree.",
    ),
    base_ref: str | None = typer.Option(
        None,
        "--base-ref",
        help=("Git ref to base the worktree on. When omitted, uses the current branch or config worktree.base_ref."),
    ),
    wip: bool = typer.Option(
        False,
        "--wip/--no-wip",
        help=("Include uncommitted working-tree changes in the worktree (tracked + untracked; not ignored)."),
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Create an isolated git worktree."""
    context: CliContext = ctx.obj["context"]
    result = worktree_create_command(context, name=name, base_ref=base_ref, wip=wip, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@worktree_app.command("list")
def worktree_list(
    ctx: typer.Context,
    status: Annotated[
        WorktreeStatus | None,
        typer.Option(
            "--status",
            help="Filter by lifecycle status (active, merged, cleaned, conflict).",
            case_sensitive=False,
        ),
    ] = None,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """List tracked worktrees and their lifecycle status."""
    context: CliContext = ctx.obj["context"]
    result = worktree_list_command(
        context,
        status=status.value if status is not None else None,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


@worktree_app.command("show")
def worktree_show(
    ctx: typer.Context,
    worktree_id: str = typer.Argument(..., help="Worktree id to show."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Show full detail for one tracked worktree."""
    context: CliContext = ctx.obj["context"]
    result = worktree_show_command(context, worktree_id, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@worktree_app.command("prune")
def worktree_prune(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate pruning without mutating filesystem or DB.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force deletion of dirty orphaned directories.",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Safely prune stale worktrees, orphaned directories, and temporary branches."""
    context: CliContext = ctx.obj["context"]
    result = worktree_prune_command(context, dry_run=dry_run, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@worktree_app.command("delete")
def worktree_delete(
    ctx: typer.Context,
    worktree_id: str = typer.Argument(..., help="Worktree id to delete."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip the confirmation prompt and delete immediately.",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Delete a worktree and branch after confirmation."""
    context: CliContext = ctx.obj["context"]
    result = worktree_delete_command(context, worktree_id, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@worktree_app.command("apply")
def worktree_apply(
    ctx: typer.Context,
    worktree_id: str = typer.Argument(..., help="Worktree id to apply."),
    strategy: Annotated[
        WorktreeApplyStrategy,
        typer.Option(
            "--strategy",
            help="Apply strategy: patch (uncommitted changes) or squash (single commit).",
            case_sensitive=False,
        ),
    ] = WorktreeApplyStrategy.PATCH,
    allow_dirty: bool = typer.Option(
        False,
        "--allow-dirty",
        help="Apply changes even if the main workspace has uncommitted changes.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Check for conflicts without mutating the main workspace.",
    ),
    delete: bool = typer.Option(
        False,
        "--delete",
        "-d",
        help="Clean up worktree and delete its branch upon successful application.",
    ),
    message: str | None = typer.Option(
        None,
        "--message",
        "-m",
        help="Commit message when using squash strategy.",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Apply changes from an isolated worktree back into the main workspace."""
    context: CliContext = ctx.obj["context"]
    result = worktree_apply_command(
        context,
        worktree_id,
        strategy=strategy,
        allow_dirty=allow_dirty,
        dry_run=dry_run,
        delete=delete,
        message=message,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


@worktree_app.command("diff")
def worktree_diff(
    ctx: typer.Context,
    worktree_id: str = typer.Argument(..., help="Worktree id to diff."),
    stat: bool = typer.Option(
        False,
        "--stat",
        help="Show diffstat summary instead of full unified diff.",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Inspect differences between worktree and base commit."""
    context: CliContext = ctx.obj["context"]
    result = worktree_diff_command(context, worktree_id, stat=stat, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)
