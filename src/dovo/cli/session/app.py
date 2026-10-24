"""Typer application registration for ``dovo session`` and the top-level logs, diff and resume shortcuts."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dovo.cli.context import CliContext
from dovo.cli.options import FormatOption
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions
from dovo.core.db import SessionStatus
from dovo.core.sessions import LogStreamFilter

from .commands.diff import session_diff_command
from .commands.list import session_list_command
from .commands.logs import session_logs_command
from .commands.resume import session_resume_command
from .commands.show import session_show_command

session_app = typer.Typer(
    name="session",
    help="List, inspect and resume blueprint execution sessions.",
    invoke_without_command=True,
)


@session_app.callback(invoke_without_command=True)
def session_callback(
    ctx: typer.Context,
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of sessions to display (defaults to 20).",
    ),
    status: Annotated[
        SessionStatus | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter by session status (running, completed, failed, cancelled, paused).",
            case_sensitive=False,
        ),
    ] = None,
    format: FormatOption = OutputFormatOptions.TERMINAL,
) -> None:
    """List, inspect and resume blueprint execution sessions; with no subcommand, list them."""
    if ctx.invoked_subcommand is None:
        context: CliContext = ctx.obj["context"]
        result = session_list_command(
            context,
            limit=limit,
            status=status.value if status is not None else None,
            output_format=format,
        )
        if not result.ok:
            raise typer.Exit(code=1)


def session_list(
    ctx: typer.Context,
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of sessions to display (defaults to 20).",
    ),
    status: Annotated[
        SessionStatus | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter by session status (running, completed, failed, cancelled, paused).",
            case_sensitive=False,
        ),
    ] = None,
    format: FormatOption = OutputFormatOptions.TERMINAL,
) -> None:
    """List past blueprint execution sessions."""
    context: CliContext = ctx.obj["context"]
    result = session_list_command(
        context,
        limit=limit,
        status=status.value if status is not None else None,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


def session_show(
    ctx: typer.Context,
    session_id: str | None = typer.Argument(None, help="Session ID. If omitted, the latest session is used."),
    logs: bool = typer.Option(
        False, "--logs", help="Display log file locations and recent log lines for this session."
    ),
    format: FormatOption = OutputFormatOptions.TERMINAL,
) -> None:
    """Show detailed metadata and error messages for a session."""
    context: CliContext = ctx.obj["context"]
    result = session_show_command(
        context,
        session_id,
        logs=logs,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


def session_logs(
    ctx: typer.Context,
    session_id: str | None = typer.Argument(None, help="Session ID. If omitted, the latest session is used."),
    step: str | None = typer.Option(None, "--step", help="Show raw output for this step ID."),
    attempt: int | None = typer.Option(None, "--attempt", help="Attempt number to show (defaults to the latest)."),
    stream: Annotated[
        LogStreamFilter,
        typer.Option(
            "--stream", help="Output stream to show with --step (stdout, stderr, both).", case_sensitive=False
        ),
    ] = LogStreamFilter.BOTH,
    tail: int | None = typer.Option(None, "--tail", min=0, help="Show only the last N lines or events."),
    format: FormatOption = OutputFormatOptions.TERMINAL,
) -> None:
    """Show a session's timeline, or one step's raw stdout/stderr with --step."""
    context: CliContext = ctx.obj["context"]
    result = session_logs_command(
        context,
        session_id,
        step=step,
        attempt=attempt,
        stream=stream.value,
        tail=tail,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


def session_diff(
    ctx: typer.Context,
    session_id: str | None = typer.Argument(
        None,
        help="Session identifier (e.g. dovo_a1b2c3d4). If omitted, displays the latest session diff.",
    ),
    patch: bool = typer.Option(
        False,
        "--patch",
        help="Write the session's plain patch to stdout, with no header, colour or truncation.",
    ),
    full: bool = typer.Option(
        False,
        "--full/--no-full",
        help="Bypass line truncation limits in interactive terminals.",
    ),
    format: FormatOption = OutputFormatOptions.TERMINAL,
) -> None:
    """View syntax-highlighted unified diff for a session."""
    if patch and format == OutputFormatOptions.JSON:
        ctx.fail("--patch cannot be combined with --format json.")
    context: CliContext = ctx.obj["context"]
    result = session_diff_command(
        context,
        session_id=session_id,
        patch=patch,
        full=full,
        output_format=format,
    )
    if not result.ok:
        raise typer.Exit(code=1)


def session_resume(
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
    answer: Annotated[
        str | None,
        typer.Option(
            "--answer", help="Answer the pending prompt as <step_id>=<value> (multiselect: JSON array or comma list)."
        ),
    ] = None,
    comment: Annotated[str | None, typer.Option("--comment", help="Comment accompanying --answer.")] = None,
    comment_file: Annotated[
        Path | None, typer.Option("--comment-file", help="Read the comment accompanying --answer from this file.")
    ] = None,
    answers_file: Annotated[
        Path | None,
        typer.Option(
            "--answers-file",
            help="JSON object with step_id, selected, comment and text; excludes the other answer flags.",
        ),
    ] = None,
    format: FormatOption = OutputFormatOptions.TERMINAL,
    display: Annotated[
        DisplayFormatOptions, typer.Option(help="Display format: 'ansi' or 'live'")
    ] = DisplayFormatOptions.ANSI,
) -> None:
    """Resume a paused blueprint execution session."""
    context: CliContext = ctx.obj["context"]
    result = session_resume_command(
        context,
        session_id=session_id,
        no_tty=no_tty,
        env_mode=env_mode,
        env_passthrough=env_passthrough,
        answer=answer,
        comment=comment,
        comment_file=comment_file,
        answers_file=answers_file,
        output_format=format,
        display_format=display,
    )
    if not result.ok:
        raise typer.Exit(code=1)


session_app.command("list")(session_list)
session_app.command("ls", hidden=True)(session_list)
session_app.command("show")(session_show)
session_app.command("logs")(session_logs)
session_app.command("diff")(session_diff)
session_app.command("resume")(session_resume)


def register_session_shortcuts(app: typer.Typer) -> None:
    """Register the top-level logs, diff and resume shortcuts with the same handlers as their session forms."""
    app.command("logs")(session_logs)
    app.command("diff")(session_diff)
    app.command("resume")(session_resume)
