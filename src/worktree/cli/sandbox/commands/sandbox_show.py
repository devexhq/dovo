"""Sandbox show command handler."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.sandbox import (
    Sandbox,
)
from worktree.core.sandbox.models import SandboxShowResult


def sandbox_show_command(
    context: CliContext,
    sandbox_id: str,
    output_format: str = "terminal",
) -> SandboxShowResult:
    """Show detail for one tracked sandbox.

    Read-only aside from reconciling a stale ``active`` row whose sandbox
    directory was removed out-of-band.

    Args:
        context: CLI context instance.
        sandbox_id: Sandbox primary key to show.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Sandbox(context.paths, db=context.db.sandboxes).show(sandbox_id)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
