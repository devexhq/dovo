"""Orchestration logic for `dovo artifacts list` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.artifacts import Artifacts, ArtifactsListResult


def artifacts_list_command(
    context: CliContext,
    *,
    session_id: str | None = None,
    output_format: str = "terminal",
) -> ArtifactsListResult:
    """List artifacts for the current project, optionally filtered to one session."""
    result = Artifacts(context.paths, db=context.db.artifacts).list(session_id=session_id)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
