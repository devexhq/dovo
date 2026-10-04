"""Typer application registration for ``dovo logs``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.core.sessions import LogStreamFilter

from .commands.show import logs_show_command

logs_app = typer.Typer(
    name="logs",
    help="Show a session's run timeline or per-step output logs.",
    invoke_without_command=True,
    # Group callbacks stop option parsing at the first positional; allow options after <session_id>.
    context_settings={"allow_interspersed_args": True},
)


@logs_app.callback(invoke_without_command=True)
def logs_callback(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID whose logs to show."),
    step: str | None = typer.Option(None, "--step", help="Show raw output for this step ID."),
    attempt: int | None = typer.Option(None, "--attempt", help="Attempt number to show (defaults to the latest)."),
    stream: Annotated[
        LogStreamFilter,
        typer.Option(
            "--stream", help="Output stream to show with --step (stdout, stderr, both).", case_sensitive=False
        ),
    ] = LogStreamFilter.BOTH,
    tail: int | None = typer.Option(None, "--tail", min=0, help="Show only the last N lines or events."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Show a session's run timeline, or one step's raw stdout/stderr with --step."""
    context: CliContext = ctx.obj["context"]
    result = logs_show_command(
        context,
        session_id,
        step=step,
        attempt=attempt,
        stream=stream.value,
        tail=tail,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)
