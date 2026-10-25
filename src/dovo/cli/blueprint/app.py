"""Typer application registration for ``dovo blueprint``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.cli.options import FormatOption
from dovo.common.models import OutputFormatOptions
from dovo.core.catalog.models import CatalogValidateStatus

from .commands.blueprint_create import blueprint_create_command
from .commands.blueprint_delete import blueprint_delete_command
from .commands.blueprint_list import blueprint_list_command
from .commands.blueprint_show import blueprint_show_command
from .commands.blueprint_validate import blueprint_validate_command

blueprint_app = typer.Typer(
    name="blueprint",
    help="Inspect, index, and manage executable blueprints across all catalog tiers.",
)


@blueprint_app.command("list")
@blueprint_app.command("ls")
def blueprint_list(
    ctx: typer.Context,
    format: FormatOption = OutputFormatOptions.TERMINAL,
):
    """List blueprint catalog items across all tiers."""
    context: CliContext = ctx.obj["context"]
    result = blueprint_list_command(context, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@blueprint_app.command("show")
def blueprint_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Blueprint SHA or name to show."),
    format: FormatOption = OutputFormatOptions.TERMINAL,
):
    """Show metadata and definition content of a blueprint."""
    context: CliContext = ctx.obj["context"]
    result = blueprint_show_command(context, name, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@blueprint_app.command("create")
def blueprint_create(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Name for the new blueprint file."),
    user: Annotated[
        bool, typer.Option("--user", help="Create in the personal user-tier catalog (~/.dovo/user/catalog/).")
    ] = False,
    global_: Annotated[
        bool, typer.Option("--global", help="Create in the shared global-tier catalog (~/.dovo/global/catalog/).")
    ] = False,
    format: FormatOption = OutputFormatOptions.TERMINAL,
):
    """Create a new blueprint at the REPO tier by default, or at USER/GLOBAL tier with --user/--global."""
    if user and global_:
        raise typer.BadParameter("--user and --global are mutually exclusive.")

    context: CliContext = ctx.obj["context"]
    result = blueprint_create_command(context, name, user=user, global_=global_, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@blueprint_app.command("delete")
def blueprint_delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Blueprint SHA or name to delete."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip deletion confirmation prompt.",
    ),
    format: FormatOption = OutputFormatOptions.TERMINAL,
):
    """Delete a repo-tier blueprint file and reindex."""
    context: CliContext = ctx.obj["context"]
    result = blueprint_delete_command(context, name, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@blueprint_app.command("validate")
def blueprint_validate(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Blueprint name, namespaced identifier, or file path to validate."),
    format: FormatOption = OutputFormatOptions.TERMINAL,
):
    """Validate a blueprint definition without executing it."""
    context: CliContext = ctx.obj["context"]
    result = blueprint_validate_command(context, target, output_format=format)
    if result.status in (
        CatalogValidateStatus.NOT_FOUND,
        CatalogValidateStatus.UNREADABLE,
        CatalogValidateStatus.TYPE_REQUIRED,
    ):
        raise typer.Exit(code=2)
    if not result.ok:
        raise typer.Exit(code=1)
