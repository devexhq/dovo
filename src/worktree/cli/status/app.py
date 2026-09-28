from __future__ import annotations

import typer

from worktree.cli.context import CliContext

from .commands.root import status_command

status_app = typer.Typer(
    name="status",
    help="Display configuration status for Worktree CLI.",
    invoke_without_command=True,
)


@status_app.callback(invoke_without_command=True)
def status_callback(
    ctx: typer.Context,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Display configuration status for Worktree CLI."""
    context: CliContext = ctx.obj["context"]
    status_command(context, output_format=format)
