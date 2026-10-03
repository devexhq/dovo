"""Typer application registration for ``dovo step``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.core.catalog.models import CatalogValidateStatus

from .commands.step_create import step_create_command
from .commands.step_delete import step_delete_command
from .commands.step_list import step_list_command
from .commands.step_show import step_show_command
from .commands.step_validate import step_validate_command

step_app = typer.Typer(
    name="step",
    help="Inspect, index, and manage executable steps across all catalog tiers.",
)


@step_app.command("list")
@step_app.command("ls")
def step_list(
    ctx: typer.Context,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """List step catalog items across all tiers."""
    context: CliContext = ctx.obj["context"]
    result = step_list_command(context, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@step_app.command("show")
def step_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Step SHA or name to show."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Show metadata and definition content of a step."""
    context: CliContext = ctx.obj["context"]
    result = step_show_command(context, name, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@step_app.command("create")
def step_create(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Name for the new step file."),
    user: Annotated[
        bool, typer.Option("--user", help="Create in the personal user-tier catalog (~/.dovo/user/catalog/).")
    ] = False,
    global_: Annotated[
        bool, typer.Option("--global", help="Create in the shared global-tier catalog (~/.dovo/global/catalog/).")
    ] = False,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Create a new step at the REPO tier by default, or at USER/GLOBAL tier with --user/--global."""
    context: CliContext = ctx.obj["context"]
    result = step_create_command(context, name, user=user, global_=global_, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@step_app.command("delete")
def step_delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Step SHA or name to delete."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip deletion confirmation prompt.",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Delete a repo-tier step file and reindex."""
    context: CliContext = ctx.obj["context"]
    result = step_delete_command(context, name, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@step_app.command("validate")
def step_validate(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Step name, namespaced identifier, or file path to validate."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Validate a step definition without executing it."""
    context: CliContext = ctx.obj["context"]
    result = step_validate_command(context, target, output_format=format)
    if result.status in (
        CatalogValidateStatus.NOT_FOUND,
        CatalogValidateStatus.UNREADABLE,
        CatalogValidateStatus.TYPE_REQUIRED,
    ):
        raise typer.Exit(code=2)
    if not result.ok:
        raise typer.Exit(code=1)
