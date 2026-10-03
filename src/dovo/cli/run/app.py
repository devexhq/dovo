"""Typer application registration for ``dovo run``."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from dovo.cli.context import CliContext
from dovo.common.models import DisplayFormatOptions, OutputFormatOptions

from .commands.root import run_command


class RunTyperGroup(TyperGroup):
    """Custom TyperGroup allowing extra args forwarding when invoked without subcommands."""

    def invoke(self, ctx: Any) -> Any:
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
        help="Retain worktree after execution.",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Override default target agent adapter.",
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
    format: Annotated[
        OutputFormatOptions, typer.Option(help="Output format: 'terminal' or 'json'.")
    ] = OutputFormatOptions.TERMINAL,
    display: Annotated[
        DisplayFormatOptions, typer.Option(help="Display format: 'ansi' or 'live'")
    ] = DisplayFormatOptions.ANSI,
) -> None:
    """Execute a blueprint."""
    context: CliContext = ctx.obj["context"]
    result = run_command(
        context,
        name=name,
        no_worktree=no_worktree,
        keep=keep,
        agent=agent,
        session_id=session_id,
        no_tty=no_tty,
        auto_apply=auto_apply,
        cli_args=list(ctx.args),
        output_format=format,
        display_format=display,
    )
    if not result.ok:
        raise typer.Exit(code=1)
