from __future__ import annotations

import typer

from dovo.cli.context import CliContext
from dovo.cli.options import FormatOption
from dovo.common.models import OutputFormatOptions

from .commands.root import status_command

status_app = typer.Typer(
    name="status",
    help="Display configuration status for Dovo CLI.",
    invoke_without_command=True,
)


@status_app.callback(invoke_without_command=True)
def status_callback(
    ctx: typer.Context,
    format: FormatOption = OutputFormatOptions.TERMINAL,
) -> None:
    """Display configuration status for Dovo CLI."""
    context: CliContext = ctx.obj["context"]
    status_command(context, output_format=format)
