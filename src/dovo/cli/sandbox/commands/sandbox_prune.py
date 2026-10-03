"""Sandbox prune command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sandbox import Sandbox
from dovo.core.sandbox.models import SandboxPruneResult


def sandbox_prune_command(
    context: CliContext,
    *,
    dry_run: bool = False,
    force: bool = False,
    output_format: str = "terminal",
) -> SandboxPruneResult:
    """Safely prune stale sandboxes, orphaned directories, and temporary branches.

    Args:
        context: CLI context instance.
        dry_run: When True, preview actions without mutating filesystem or DB.
        force: When True, delete dirty orphaned directories containing uncommitted files.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        Structured prune result.
    """
    result = Sandbox(
        context.paths,
        db=context.db.sandboxes,
        runs_db=context.db.runs,
    ).prune(
        dry_run=dry_run,
        force=force,
    )

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
