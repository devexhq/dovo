from typing import Annotated

import typer

from dovo.cli.options import FormatOption
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import OutputFormatOptions
from dovo.common.version import get_version

from .commands.root import init_command

init_app = typer.Typer(name="init", help="Initialize Dovo CLI in the current directory.")


@init_app.callback(invoke_without_command=True)
def init_callback(
    ctx: typer.Context,
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing config with fresh V1 defaults (destructive).",
    ),
    repair: bool = typer.Option(
        False,
        "--repair",
        help="Add missing required config keys without overwriting user values.",
    ),
    format: FormatOption = OutputFormatOptions.TERMINAL,
    id: Annotated[
        str | None,
        typer.Option(help="Explicit unique project ID slug."),
    ] = None,
    display_name: Annotated[
        str | None,
        typer.Option(help="Human-readable project display name."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(help="Overwrite an existing project.json's id when --id is also provided."),
    ] = False,
):
    """Provision a secure local hidden folder path and tracking schemas."""
    root_dir = Filesystem(ctx.obj["path"]).root_dir
    result = init_command(
        root_dir,
        tool_version=get_version(),
        overwrite=overwrite,
        repair=repair,
        output_format=format,
        project_id=id,
        display_name=display_name,
        force=force,
    )
    if result.error_code == ErrorCode.PROJECT_ID_INVALID:
        raise typer.Exit(code=2)
    if not result.ok:
        raise typer.Exit(code=1)
