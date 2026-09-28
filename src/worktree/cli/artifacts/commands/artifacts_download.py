"""Orchestration logic for `wt artifacts download` CLI command."""

from __future__ import annotations

from pathlib import Path

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.artifacts import ArtifactDownloadResult, Artifacts


def artifacts_download_command(
    context: CliContext,
    session_id: str,
    name: str,
    *,
    dest: str,
    output_format: str = "terminal",
) -> ArtifactDownloadResult:
    """Download and checksum-verify a named artifact bundle into dest."""
    result = Artifacts(context.paths, db=context.db.artifacts).download(session_id, name, dest=Path(dest))
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
