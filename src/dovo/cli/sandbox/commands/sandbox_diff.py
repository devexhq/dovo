"""Sandbox diff command handler."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.sandbox import Sandbox
from dovo.core.sandbox.models import SandboxDiffResult


def sandbox_diff_command(
    context: CliContext,
    sandbox_id: str,
    *,
    stat: bool = False,
    output_format: str = "terminal",
) -> SandboxDiffResult:
    """Inspect unified diff or file summary statistics for a sandbox.

    Args:
        context: CLI context instance.
        sandbox_id: Sandbox primary key to diff.
        stat: When True, show diffstat summary instead of full unified diff.
        output_format: Presentation format ("terminal" or "json").
    """
    sandbox = Sandbox(context.paths, db=context.db.sandboxes)
    result = sandbox.diff(sandbox_id, stat=stat)

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
