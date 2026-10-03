"""Sandbox apply command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sandbox import Sandbox, SandboxApplyResult, SandboxApplyStrategy


def sandbox_apply_command(
    context: CliContext,
    sandbox_id: str,
    *,
    strategy: SandboxApplyStrategy = SandboxApplyStrategy.PATCH,
    allow_dirty: bool = False,
    dry_run: bool = False,
    delete: bool = False,
    message: str | None = None,
    output_format: str = "terminal",
) -> SandboxApplyResult:
    """Apply sandbox changes back to main workspace.

    Args:
        context: CLI context instance.
        sandbox_id: Sandbox primary key to apply.
        strategy: Apply strategy ('patch' or 'squash').
        allow_dirty: Allow application even if main repository is dirty.
        dry_run: Perform conflict check without mutating workspace.
        delete: Clean up sandbox upon successful application.
        message: Optional commit message for squash strategy.
        output_format: Presentation format ("terminal" or "json").
    """
    sandbox = Sandbox(context.paths, db=context.db.sandboxes)
    result = sandbox.apply(
        sandbox_id=sandbox_id,
        strategy=strategy,
        allow_dirty=allow_dirty,
        dry_run=dry_run,
        delete=delete,
        message=message,
    )

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
