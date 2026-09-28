"""Sandbox list command handler."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.sandbox import (
    Sandbox,
)
from worktree.core.sandbox.models import SandboxListResult


def sandbox_list_command(
    context: CliContext,
    status: str | None = None,
    output_format: str = "terminal",
) -> SandboxListResult:
    """List tracked sandboxes with lifecycle status.

    Read-only aside from reconciling stale ``active`` rows whose sandbox
    directory was removed out-of-band.

    Args:
        context: CLI context instance.
        status: Optional status filter validated by Typer at the CLI layer.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Sandbox(context.paths, db=context.db.sandboxes).list(status=status)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
