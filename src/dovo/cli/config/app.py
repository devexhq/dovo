import typer

from dovo.cli.context import CliContext

from .commands.config_set import config_set_command
from .commands.config_show import config_show_command
from .commands.config_unset import config_unset_command
from .commands.config_validate import config_validate_command

config_app = typer.Typer(
    name="config",
    help="Inspect, update, and validate Dovo CLI configuration.",
)


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Display the full normalized effective configuration as JSON."""
    context: CliContext = ctx.obj["context"]
    result = config_show_command(context, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(
        ...,
        help="Config key or nested dot-path (e.g. agent.model).",
    ),
    value: str = typer.Argument(
        ...,
        help="Value to store (string; typed parsing is separate).",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Set a configuration value by key or nested dot-path."""
    context: CliContext = ctx.obj["context"]
    result = config_set_command(context, key, value, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@config_app.command("unset")
def config_unset(
    ctx: typer.Context,
    key: str = typer.Argument(
        ...,
        help="Config key or nested dot-path (e.g. agent.model).",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Remove a configuration value by key or nested dot-path, falling back to its schema default."""
    context: CliContext = ctx.obj["context"]
    result = config_unset_command(context, key, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@config_app.command("validate")
def config_validate(
    ctx: typer.Context,
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
):
    """Validate .dovo/config.json against the V1 schema and semantic rules."""
    context: CliContext = ctx.obj["context"]
    result = config_validate_command(context, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)
