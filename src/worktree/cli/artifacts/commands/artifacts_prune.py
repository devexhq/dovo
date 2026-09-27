"""Orchestration logic for `wt artifacts prune` CLI command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.artifacts import Artifacts, ArtifactsPruneResult


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
    result = Artifacts(path=context.cwd, db=context.db.artifacts).prune(
        dry_run=dry_run,
        force=force,
        config=context.config,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
