"""Typer CLI entrypoint for ``dovo doctor``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.core.doctor import CheckCategory

from .commands.root import doctor_command

doctor_app = typer.Typer(
    name="doctor",
    help="Run registered diagnostic checks and print a scannable workspace health report.",
    invoke_without_command=True,
)


@doctor_app.callback(invoke_without_command=True)
def doctor_callback(
    ctx: typer.Context,
    category: Annotated[
        CheckCategory | None,
        typer.Option(
            "--category",
            help="Restrict execution to a single check category "
            "(git, config, filesystem, worktree, agent, environment).",
            case_sensitive=False,
        ),
    ] = None,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Run registered diagnostic checks, dispatch the report, and exit 1 when any check failed."""
    context: CliContext = ctx.obj["context"]

    categories = [category] if category is not None else None
    result = doctor_command(context, categories=categories, output_format=format)

    if not result.ok:
        raise typer.Exit(code=1)
