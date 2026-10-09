"""Typer application registration for ``dovo resume``."""

from __future__ import annotations

from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions

from .commands.root import resume_command

resume_app = typer.Typer(
    name="resume",
    help="Resume a paused blueprint execution session.",
    invoke_without_command=True,
    context_settings={"allow_interspersed_args": True},
)


@resume_app.callback(invoke_without_command=True)
def resume_callback(
    ctx: typer.Context,
    session_id: str | None = typer.Argument(
        None,
        help="Session identifier to resume. If omitted, the latest paused session is resumed.",
    ),
    no_tty: bool = typer.Option(
        False,
        "--no-tty",
        help="Disable interactive prompts; prompt_user failures abort the run.",
    ),
    env_mode: Annotated[
        AgentEnvModeOptions | None,
        typer.Option("--env-mode", help="Override agent.env_mode for this invocation: 'allowlist' or 'inherit'."),
    ] = None,
    env_passthrough: Annotated[
        list[str] | None,
        typer.Option(
            "--env-passthrough",
            help="Forward a host variable name or trailing-'*' prefix to the agent for this invocation; repeatable.",
        ),
    ] = None,
    format: Annotated[
        OutputFormatOptions, typer.Option(help="Output format: 'terminal' or 'json'.")
    ] = OutputFormatOptions.TERMINAL,
    display: Annotated[
        DisplayFormatOptions, typer.Option(help="Display format: 'ansi' or 'live'")
    ] = DisplayFormatOptions.ANSI,
) -> None:
    """Resume a paused blueprint execution session."""
    context: CliContext = ctx.obj["context"]
    result = resume_command(
        context,
        session_id=session_id,
        no_tty=no_tty,
        env_mode=env_mode,
        env_passthrough=env_passthrough,
        output_format=format,
        display_format=display,
    )
    if not result.ok:
        raise typer.Exit(code=1)
