"""Typer CLI entrypoint for the Worktree (`wt`) command."""

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from worktree.cli.artifacts.app import artifacts_app
from worktree.cli.blueprint.app import blueprint_app
from worktree.cli.config.app import config_app
from worktree.cli.context import CliContext, default_lock_wait_notifier, ensure_lazy_project_init
from worktree.cli.diff.app import register_diff_command
from worktree.cli.doctor.app import doctor_app
from worktree.cli.history.app import history_app
from worktree.cli.init.app import init_app
from worktree.cli.logs.app import logs_app
from worktree.cli.resume.app import resume_app
from worktree.cli.run.app import run_app
from worktree.cli.sandbox.app import sandbox_app
from worktree.cli.status.app import status_app
from worktree.cli.step.app import step_app
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.cli.ui.events import ErrorPanelEvent, MessageEvent, WelcomeBannerEvent
from worktree.common.filesystem import Filesystem
from worktree.common.lock import LockTimeoutError, WorkspaceLock
from worktree.common.version import get_version
from worktree.core.config import ConfigLoadError

# Package Metadata matching our PyPI footprint
__version__ = get_version()

# Commands that must operate without a valid config (see CliContext.build's load_config param)
NON_STRICT_CONFIG_COMMANDS = {"config", "doctor", "init", "status"}


class WorktreeTyperGroup(TyperGroup):
    """Custom TyperGroup capturing subcommand args for context initialization."""

    def invoke(self, ctx: Any) -> Any:
        """Capture help flags before executing command callbacks."""
        if getattr(ctx, "_protected_args", None) or getattr(ctx, "args", None):
            raw_args = [*ctx._protected_args, *ctx.args]
            ctx.ensure_object(dict)
            ctx.obj["is_help"] = any(a in ("--help", "-h") for a in raw_args)
        return super().invoke(ctx)


# Initialize Typer App with clean configuration defaults
app = typer.Typer(
    cls=WorktreeTyperGroup,
    name="wt",
    help="Isolated git worktree developer workflows and autonomous AI agent workspaces.",
    add_completion=True,
    rich_markup_mode="rich",
)

app.add_typer(artifacts_app, name="artifacts")
app.add_typer(blueprint_app, name="blueprint")
app.add_typer(config_app, name="config")
register_diff_command(app)
app.add_typer(doctor_app, name="doctor")
app.add_typer(history_app, name="history")
app.add_typer(init_app, name="init")
app.add_typer(logs_app, name="logs")
app.add_typer(resume_app, name="resume")
app.add_typer(run_app, name="run")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(status_app, name="status")
app.add_typer(step_app, name="step")


def print_welcome_banner() -> None:
    """Renders a highly scannable, developer-focused ASCII brand panel."""
    ui_dispatcher.dispatch(WelcomeBannerEvent(version=__version__))


def version_callback(value: bool) -> None:
    """Callback function to handle explicit version printing flags."""
    if value:
        ui_dispatcher.dispatch(MessageEvent(message=f"[bold green]Worktree CLI[/bold green] v{__version__}"))
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Target workspace root directory (defaults to auto-discovering worktree or git root).",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable extensive internal engineering telemetry logging.",
        ),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Print the current version of the Worktree CLI and exit.",
        ),
    ] = None,
):
    """Global configuration wrapper managing shared application context."""
    # Stash verbose and path settings inside the runtime context dict for downstream commands
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["path"] = path

    # Initialize default lock wait notifier for cross-process concurrency feedback
    WorkspaceLock.set_default_on_wait(default_lock_wait_notifier)

    # 1. Handle base commands
    if ctx.invoked_subcommand is None:
        print_welcome_banner()
        ui_dispatcher.dispatch(MessageEvent(message=ctx.get_help()))
        raise typer.Exit()
    elif verbose:
        ui_dispatcher.dispatch(
            MessageEvent(message="[dim yellow][TELEMETRY] Global verbose tracking layer active.[/dim yellow]")
        )

    # 2. Build the shared CLI context for every real subcommand invocation
    if not ctx.obj.get("is_help", False):
        try:
            if ctx.invoked_subcommand == "run":
                ensure_lazy_project_init(Filesystem(path))
            ctx.obj["context"] = CliContext.build(
                path=path, load_config=ctx.invoked_subcommand not in NON_STRICT_CONFIG_COMMANDS
            )
        except ConfigLoadError as exc:
            ui_dispatcher.dispatch(exc.result)
            raise typer.Exit(code=1) from exc


def run_cli() -> None:
    """Main entrypoint with global crash protection."""
    try:
        app()
    except typer.Exit:
        # Allow intentional Typer exits (like version_callback or help) to pass through normally
        raise
    except LockTimeoutError as exc:
        ui_dispatcher.dispatch(
            ErrorPanelEvent(
                title="Workspace Lock Timeout",
                message=str(exc),
                border_style="red",
            )
        )
        sys.exit(1)
    except ConfigLoadError as exc:
        ui_dispatcher.dispatch(exc.result)
        sys.exit(1)
    except Exception as exc:
        # Global Catch-All for unexpected bugs (e.g., missing record.id)
        ui_dispatcher.dispatch(
            ErrorPanelEvent(
                title="Fatal Error",
                message=f"A fatal unexpected error occurred.\nDetails: {exc!s}",
                border_style="red",
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    run_cli()
