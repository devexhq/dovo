from typing import Annotated

import typer

from dovo.common.filesystem import Filesystem
from dovo.common.version import get_version
from dovo.core.bootstrap import InitFailureMode

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
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
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
    if result.failure_mode == InitFailureMode.INVALID_PROJECT_ID:
        raise typer.Exit(code=2)
    if not result.ok:
        raise typer.Exit(code=1)
