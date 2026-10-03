"""Typer application registration for ``dovo resume``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.common.models import DisplayFormatOptions, OutputFormatOptions

from .commands.root import resume_command

resume_app = typer.Typer(
    name="resume",
    help="Resume a paused blueprint execution session.",
    invoke_without_command=True,
    context_settings={"allow_interspersed_args": True},
)


@resume_app.callback(invoke_without_command=True)
def resume_callback(
    ctx: typer.Context,
    session_id: str | None = typer.Argument(
        None,
        help="Session identifier to resume. If omitted, the latest paused session is resumed.",
    ),
    no_tty: bool = typer.Option(
        False,
        "--no-tty",
        help="Disable interactive prompts; prompt_user failures abort the run.",
    ),
    format: Annotated[
        OutputFormatOptions, typer.Option(help="Output format: 'terminal' or 'json'.")
    ] = OutputFormatOptions.TERMINAL,
    display: Annotated[
        DisplayFormatOptions, typer.Option(help="Display format: 'ansi' or 'live'")
    ] = DisplayFormatOptions.ANSI,
) -> None:
    """Resume a paused blueprint execution session."""
    context: CliContext = ctx.obj["context"]
    result = resume_command(context, session_id=session_id, no_tty=no_tty, output_format=format, display_format=display)
    if not result.ok:
        raise typer.Exit(code=1)
