"""Typer application registration for ``dovo run``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer
from typer.core import TyperGroup

if TYPE_CHECKING:
    # TyperGroup.invoke is annotated with Typer's vendored click Context, which has no public name.
    from typer._click.core import Context

from dovo.cli.context import CliContext
from dovo.cli.options import FormatOption
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions

from .commands.root import run_command


class RunTyperGroup(TyperGroup):
    """Custom TyperGroup allowing extra args forwarding when invoked without subcommands."""

    def invoke(self, ctx: Context) -> object:
        """Forward protected args to ctx.args when no subcommands are registered."""
        if not self.commands and self.invoke_without_command:
            ctx.args = [*getattr(ctx, "_protected_args", []), *ctx.args]
            ctx._protected_args = []
            with ctx:
                return super(TyperGroup, self).invoke(ctx)
        return super().invoke(ctx)


run_app = typer.Typer(
    cls=RunTyperGroup,
    name="run",
    help="Execute any blueprint by name.",
    invoke_without_command=True,
    context_settings={
        "allow_interspersed_args": True,
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    },
)


@run_app.callback(invoke_without_command=True)
def run_callback(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Blueprint name to run."),
    no_worktree: bool = typer.Option(
        False,
        "--no-worktree",
        help="Run execution in-place in the working tree without creating a Git worktree.",
    ),
    keep: bool = typer.Option(
        False,
        "--keep",
        help="Keep the worktree whatever the run status.",
    ),
    discard: bool = typer.Option(
        False,
        "--discard",
        help="Remove the worktree whatever the run status, including after a failed run.",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Select the provider whose agents.<provider> settings the run uses.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Explicit session identifier.",
    ),
    no_tty: bool = typer.Option(
        False,
        "--no-tty",
        help="Disable interactive prompts; prompt_user failures abort the run.",
    ),
    auto_apply: bool = typer.Option(
        False,
        "--auto-apply",
        help="Automatically apply worktree changes to the main workspace on successful completion.",
    ),
    env_mode: Annotated[
        AgentEnvModeOptions | None,
        typer.Option(
            "--env-mode", help="Override agents.<provider>.env_mode for this invocation: 'allowlist' or 'inherit'."
        ),
    ] = None,
    env_passthrough: Annotated[
        list[str] | None,
        typer.Option(
            "--env-passthrough",
            help="Forward a host variable name or trailing-'*' prefix to the agent for this invocation; repeatable.",
        ),
    ] = None,
    format: FormatOption = OutputFormatOptions.TERMINAL,
    display: Annotated[
        DisplayFormatOptions, typer.Option(help="Display format: 'ansi' or 'live'")
    ] = DisplayFormatOptions.ANSI,
) -> None:
    """Execute a blueprint."""
    if keep and discard:
        raise typer.BadParameter("--keep and --discard are mutually exclusive.")

    context: CliContext = ctx.obj["context"]
    result = run_command(
        context,
        name=name,
        no_worktree=no_worktree,
        keep=keep,
        discard=discard,
        agent=agent,
        session_id=session_id,
        no_tty=no_tty,
        auto_apply=auto_apply,
        cli_args=list(ctx.args),
        env_mode=env_mode,
        env_passthrough=env_passthrough,
        output_format=format,
        display_format=display,
    )
    if not result.ok:
        raise typer.Exit(code=1)
