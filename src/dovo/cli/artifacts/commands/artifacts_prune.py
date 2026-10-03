"""Orchestration logic for `dovo artifacts prune` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.artifacts import Artifacts, ArtifactsPruneResult


def artifacts_prune_command(
    context: CliContext,
    *,
    dry_run: bool = False,
    force: bool = False,
    output_format: str = "terminal",
) -> ArtifactsPruneResult:
    """Delete expired artifact bundles per prune.remove_expired_artifacts, or preview under dry_run.

    Args:
        context: CLI context instance.
        dry_run: When True, preview actions without mutating filesystem or DB.
        force: When True, prune expired artifacts even when the config toggle is disabled.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        Structured prune result.
    """
    result = Artifacts(context.paths, db=context.db.artifacts).prune(
        dry_run=dry_run,
        force=force,
        config=context.config,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
