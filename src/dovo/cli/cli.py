"""Typer CLI entrypoint for the Dovo (`dovo`) command."""

import importlib
from collections.abc import Iterator, MutableMapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

# Typer's vendored click exceptions have no public name; usage errors and exits must pass through the failure handler.
from typer._click.exceptions import Abort, ClickException, Exit
from typer.core import TyperGroup

if TYPE_CHECKING:
    # TyperGroup is annotated with Typer's vendored click types, which have no public name.
    from typer._click.core import Command, Context

from dovo.cli.context import CliContext, ensure_lazy_project_init
from dovo.cli.outcome import exit_for
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.cli.ui.events import ErrorEvent, LockWaitEvent, MessageEvent, WelcomeBannerEvent
from dovo.common.diagnostic_log import DiagnosticLevel, bind_cli_command, record_exception, reset_cli_command
from dovo.common.exceptions import (
    FailureFields,
    WorkspaceError,
    escaped_failure_fields,
    fatal_failure_fields,
)
from dovo.common.filesystem import Filesystem
from dovo.common.interrupt import install_interrupt_handlers, reraise_interrupt_signal
from dovo.common.lock import LockTimeoutError, LockWaitNotifier, WorkspaceLock
from dovo.common.models import OutputFormatOptions
from dovo.common.version import get_version
from dovo.core.config import ConfigError

# Package Metadata matching our PyPI footprint
__version__ = get_version()

# Commands that tolerate a missing or broken config (see CliContext.build's load_config param); a project identity is still required
NON_STRICT_CONFIG_COMMANDS = {"config", "doctor", "init", "status"}

# Each command group's module and Typer app, in help order. A group is imported the first time it is looked up (to
# run it, or to list it for help or completion), so a command does not pay for importing every other group.
COMMAND_GROUPS = {
    "artifacts": "dovo.cli.artifacts.app:artifacts_app",
    "blueprint": "dovo.cli.blueprint.app:blueprint_app",
    "config": "dovo.cli.config.app:config_app",
    "doctor": "dovo.cli.doctor.app:doctor_app",
    "init": "dovo.cli.init.app:init_app",
    "run": "dovo.cli.run.app:run_app",
    "session": "dovo.cli.session.app:session_app",
    "worktree": "dovo.cli.worktree.app:worktree_app",
    "status": "dovo.cli.status.app:status_app",
    "step": "dovo.cli.step.app:step_app",
}

# Root shortcuts that run the session command of the same name, listed before the groups in help.
SESSION_SHORTCUTS = ("logs", "diff", "resume")


class LazyCommands(MutableMapping[str, "Command"]):
    """The root group's commands by name, each built from its module the first time it is looked up."""

    def __init__(self, commands: MutableMapping[str, "Command"]) -> None:
        self._loaded: dict[str, Command] = dict(commands)

    def __getitem__(self, name: str) -> "Command":
        if name not in self._loaded:
            self._loaded[name] = self._load(name)
        return self._loaded[name]

    def _load(self, name: str) -> "Command":
        """Build the named group from its Typer app, or return the session command a shortcut runs."""
        if name in SESSION_SHORTCUTS:
            session_group = self["session"]
            if not isinstance(session_group, TyperGroup):
                raise TypeError("the session command must be a group to provide its shortcuts")
            return session_group.commands[name]
        if name not in COMMAND_GROUPS:
            raise KeyError(name)
        module_name, attribute = COMMAND_GROUPS[name].split(":")
        return typer.main.get_group(getattr(importlib.import_module(module_name), attribute))

    def __contains__(self, name: object) -> bool:
        return name in self._loaded or name in SESSION_SHORTCUTS or name in COMMAND_GROUPS

    def __setitem__(self, name: str, command: "Command") -> None:
        self._loaded[name] = command

    def __delitem__(self, name: str) -> None:
        del self._loaded[name]

    def __iter__(self) -> Iterator[str]:
        return iter(dict.fromkeys([*SESSION_SHORTCUTS, *COMMAND_GROUPS, *self._loaded]))

    def __len__(self) -> int:
        return len(dict.fromkeys([*SESSION_SHORTCUTS, *COMMAND_GROUPS, *self._loaded]))


class DovoTyperGroup(TyperGroup):
    """Root group that loads command groups lazily and captures subcommand args for context initialization."""

    @property
    def commands(self) -> MutableMapping[str, "Command"]:
        """The group's commands, loaded on first lookup."""
        return self._lazy_commands

    @commands.setter
    def commands(self, commands: MutableMapping[str, "Command"]) -> None:
        self._lazy_commands = LazyCommands(commands)

    def list_commands(self, ctx: "Context") -> list[str]:
        """List every command name without importing the groups."""
        return list(self.commands)

    def invoke(self, ctx: "Context") -> object:
        """Capture help flags and the requested format, announce lock waits in it, then run the command, reporting a failure no command result carries and exiting 1."""
        raw_args = [*getattr(ctx, "_protected_args", []), *getattr(ctx, "args", [])]
        ctx.ensure_object(dict)
        if raw_args:
            ctx.obj["is_help"] = any(a in ("--help", "-h") for a in raw_args)
        output_format = _requested_format(raw_args)
        ctx.obj["output_format"] = output_format
        WorkspaceLock.set_default_on_wait(_lock_wait_notifier(output_format))
        try:
            return super().invoke(ctx)
        except (ClickException, Exit, Abort):
            raise
        except LockTimeoutError as error:
            _report_failure("Workspace Lock Timeout", escaped_failure_fields(error), output_format)
        except WorkspaceError as error:
            _report_failure("Workspace Not Initialized", escaped_failure_fields(error), output_format)
        except ConfigError as error:
            _report_failure("Invalid Configuration", escaped_failure_fields(error), output_format)
        except Exception as error:
            record = record_exception(error, level=DiagnosticLevel.ERROR, operation="cli.run")
            _report_failure("Fatal Error", fatal_failure_fields(error, record), output_format)
        raise typer.Exit(code=1)


def _requested_format(args: Sequence[str]) -> OutputFormatOptions:
    """Return the output format the command line asks for: JSON for `--format json` or `--format=json`, else terminal."""
    requests_json = "--format=json" in args or any(
        flag == "--format" and value == "json" for flag, value in pairwise(args)
    )
    return OutputFormatOptions.JSON if requests_json else OutputFormatOptions.TERMINAL


def _lock_wait_notifier(output_format: OutputFormatOptions) -> LockWaitNotifier:
    """Return the WorkspaceLock wait callback that dispatches LockWaitEvent in output_format."""

    def notify(lock_path: Path, holder_pid: str | None, timeout_seconds: float) -> None:
        event = LockWaitEvent(lock_path=str(lock_path), holder_pid=holder_pid, timeout_seconds=timeout_seconds)
        ui_dispatcher.dispatch(event, output_format=output_format)

    return notify


def _report_failure(title: str, fields: FailureFields, output_format: OutputFormatOptions) -> None:
    """Dispatch the failure as the `error` event under JSON output, else as a titled panel."""
    ui_dispatcher.dispatch(ErrorEvent(title=title, **fields), output_format=output_format)


# Initialize Typer App with clean configuration defaults
app = typer.Typer(
    cls=DovoTyperGroup,
    name="dovo",
    help="Isolated git worktree developer workflows and autonomous AI agent workspaces.",
    add_completion=True,
    rich_markup_mode="rich",
)


def print_welcome_banner() -> None:
    """Renders a highly scannable, developer-focused ASCII brand panel."""
    ui_dispatcher.dispatch(WelcomeBannerEvent(version=__version__))


def version_callback(value: bool) -> None:
    """Callback function to handle explicit version printing flags."""
    if value:
        ui_dispatcher.dispatch(MessageEvent(message=f"[bold green]Dovo CLI[/bold green] v{__version__}"))
        raise typer.Exit()


def _ensure_run_workspace(path: Path | None, output_format: OutputFormatOptions) -> None:
    """Auto-initialize the workspace for `dovo run`; on failure report it and exit by its error_code.

    Under JSON output the failure is the `error` event, since no run has started; otherwise the init result's panel.
    """
    result = ensure_lazy_project_init(Filesystem(path))
    if result is None or result.error_code is None:
        return
    if output_format == OutputFormatOptions.JSON:
        fields = FailureFields(
            error_code=result.error_code,
            errors=result.errors,
            remediations=result.remediations,
            diagnostics=result.diagnostics,
        )
        _report_failure("Initialization Failed", fields, output_format)
    else:
        ui_dispatcher.dispatch(result)
    exit_for(result)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help=(
                "Target workspace root directory (defaults to auto-discovering the nearest Dovo or git root, "
                "skipping the directory that holds DOVO_HOME)."
            ),
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
            help="Print the current version of the Dovo CLI and exit.",
        ),
    ] = None,
):
    """Global configuration wrapper managing shared application context."""
    # Stash verbose and path settings inside the runtime context dict for downstream commands
    ctx.ensure_object(dict)
    token = bind_cli_command(ctx.invoked_subcommand)
    ctx.call_on_close(lambda: reset_cli_command(token))
    ctx.obj["verbose"] = verbose
    ctx.obj["path"] = path
    output_format = ctx.obj.get("output_format", OutputFormatOptions.TERMINAL)

    # 1. Handle base commands
    if ctx.invoked_subcommand is None:
        print_welcome_banner()
        ui_dispatcher.dispatch(MessageEvent(message=ctx.get_help()))
        raise typer.Exit()
    elif verbose:
        ui_dispatcher.dispatch(
            MessageEvent(message="[TELEMETRY] Global verbose tracking layer active.", style="dim yellow"),
            output_format=output_format,
        )

    # 2. Build the shared CLI context for every real subcommand invocation
    if not ctx.obj.get("is_help", False) and ctx.invoked_subcommand != "init":
        if ctx.invoked_subcommand == "run":
            _ensure_run_workspace(path, output_format)
        ctx.obj["context"] = CliContext.build(
            path=path, load_config=ctx.invoked_subcommand not in NON_STRICT_CONFIG_COMMANDS
        )


def run_cli() -> None:
    """Main entrypoint: route interrupts to KeyboardInterrupt and, once an interrupted command has unwound, end the process by the signal it received."""
    install_interrupt_handlers()
    try:
        app()
    finally:
        reraise_interrupt_signal()


if __name__ == "__main__":
    run_cli()
