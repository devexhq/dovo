from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.core.db import SandboxStatus
from dovo.core.sandbox.models import SandboxApplyStrategy

from .commands.sandbox_apply import sandbox_apply_command
from .commands.sandbox_create import sandbox_create_command
from .commands.sandbox_delete import sandbox_delete_command
from .commands.sandbox_diff import sandbox_diff_command
from .commands.sandbox_list import sandbox_list_command
from .commands.sandbox_prune import sandbox_prune_command
from .commands.sandbox_show import sandbox_show_command

sandbox_app = typer.Typer(
    name="sandbox",
    help="Inspect and manage git worktree sandboxes.",
)


@sandbox_app.command("create")
def sandbox_create(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None,
        "--name",
        help="Optional human-readable name for the sandbox.",
    ),
    base_ref: str | None = typer.Option(
        None,
        "--base-ref",
        help=("Git ref to base the sandbox on. When omitted, uses the current branch or config sandbox.base_ref."),
    ),
    wip: bool = typer.Option(
        False,
        "--wip/--no-wip",
        help=("Include uncommitted working-tree changes in the sandbox (tracked + untracked; not ignored)."),
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Create an isolated git worktree sandbox."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_create_command(context, name=name, base_ref=base_ref, wip=wip, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@sandbox_app.command("list")
def sandbox_list(
    ctx: typer.Context,
    status: Annotated[
        SandboxStatus | None,
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
    """List tracked sandboxes and their lifecycle status."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_list_command(
        context,
        status=status.value if status is not None else None,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


@sandbox_app.command("show")
def sandbox_show(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox id to show."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Show full detail for one tracked sandbox."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_show_command(context, sandbox_id, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@sandbox_app.command("prune")
def sandbox_prune(
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
    """Safely prune stale sandboxes, orphaned directories, and temporary branches."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_prune_command(context, dry_run=dry_run, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@sandbox_app.command("delete")
def sandbox_delete(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox id to delete."),
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
    """Delete a sandbox worktree and branch after confirmation."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_delete_command(context, sandbox_id, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@sandbox_app.command("apply")
def sandbox_apply(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox id to apply."),
    strategy: Annotated[
        SandboxApplyStrategy,
        typer.Option(
            "--strategy",
            help="Apply strategy: patch (uncommitted changes) or squash (single commit).",
            case_sensitive=False,
        ),
    ] = SandboxApplyStrategy.PATCH,
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
        help="Clean up sandbox worktree and delete its branch upon successful application.",
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
    """Apply changes from an isolated sandbox back into the main workspace."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_apply_command(
        context,
        sandbox_id,
        strategy=strategy,
        allow_dirty=allow_dirty,
        dry_run=dry_run,
        delete=delete,
        message=message,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


@sandbox_app.command("diff")
def sandbox_diff(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox id to diff."),
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
    """Inspect differences between sandbox worktree and base commit."""
    context: CliContext = ctx.obj["context"]
    result = sandbox_diff_command(context, sandbox_id, stat=stat, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)
