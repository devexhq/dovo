"""Typer application registration for ``dovo history``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.core.db import RunStatus

from .commands.root import history_list_command, history_root_command
from .commands.show import history_show_command

history_app = typer.Typer(
    name="history",
    help="Inspect past blueprint execution sessions and step details.",
    invoke_without_command=True,
)


@history_app.callback(invoke_without_command=True)
def history_callback(
    ctx: typer.Context,
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of execution runs to display (defaults to 20).",
    ),
    status: Annotated[
        RunStatus | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter by run status (running, completed, failed, cancelled, paused).",
            case_sensitive=False,
        ),
    ] = None,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Inspect past blueprint execution sessions and step details."""
    if ctx.invoked_subcommand is None:
        context: CliContext = ctx.obj["context"]
        result = history_root_command(
            context,
            limit=limit,
            status=status.value if status is not None else None,
            output_format=format,
        )
        if not result.ok:
            raise typer.Exit(code=1)


@history_app.command("list")
def history_list(
    ctx: typer.Context,
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of execution runs to display (defaults to 20).",
    ),
    status: Annotated[
        RunStatus | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter by run status (running, completed, failed, cancelled, paused).",
            case_sensitive=False,
        ),
    ] = None,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """List past blueprint execution sessions."""
    context: CliContext = ctx.obj["context"]
    result = history_list_command(
        context,
        limit=limit,
        status=status.value if status is not None else None,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


@history_app.command("show")
def history_show(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID to inspect."),
    logs: bool = typer.Option(
        False, "--logs", help="Display log file locations and recent log lines for this session."
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Show detailed metadata and error messages for a session."""
    context: CliContext = ctx.obj["context"]
    result = history_show_command(
        context,
        session_id,
        logs=logs,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)
