"""Typer application registration for `wt artifacts`."""

from __future__ import annotations

import typer

from worktree.cli.context import CliContext

from .commands.artifacts_download import artifacts_download_command
from .commands.artifacts_list import artifacts_list_command
from .commands.artifacts_prune import artifacts_prune_command

artifacts_app = typer.Typer(
    name="artifacts",
    help="Inspect and manage published session artifacts.",
)


@artifacts_app.command("list")
def artifacts_list(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session", help="Restrict the listing to one session ID."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """List published artifacts for the current project."""
    context: CliContext = ctx.obj["context"]
    result = artifacts_list_command(context, session_id=session, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@artifacts_app.command("download")
def artifacts_download(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Session ID the artifact was published under."),
    name: str = typer.Argument(..., help="Artifact name to download."),
    dest: str = typer.Option(..., "--dest", help="Host destination directory to extract files into."),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Download and checksum-verify a published artifact bundle."""
    context: CliContext = ctx.obj["context"]
    result = artifacts_download_command(context, session_id, name, dest=dest, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)


@artifacts_app.command("prune")
def artifacts_prune(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate pruning without mutating filesystem or DB.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Prune expired artifacts even when disabled by config.",
    ),
    format: str = typer.Option(
        "terminal",
        "--format",
        help="Presentation format ('terminal' or 'json').",
    ),
) -> None:
    """Delete expired artifact bundles per prune.remove_expired_artifacts, or preview with --dry-run."""
    context: CliContext = ctx.obj["context"]
    result = artifacts_prune_command(context, dry_run=dry_run, force=force, output_format=format)
    if not result.ok:
        raise typer.Exit(code=1)
