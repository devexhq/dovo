"""Status command implementation."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.sessions import reconcile_stale_sessions
from dovo.core.status import Status
from dovo.core.status.models import DovoStatusResult


def status_command(
    context: CliContext,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> DovoStatusResult:
    """Inspect active Dovo configuration and repository context.

    Args:
        context: CLI context instance.
        output_format: Presentation format ("terminal" or "json").
    """
    reconciliation_result = reconcile_stale_sessions(context.db.sessions, path=context.paths.root_dir)

    result = Status(context.paths, git=context.git).collect()

    if reconciliation_result.warning and reconciliation_result.warning not in result.warnings:
        result.warnings.insert(0, reconciliation_result.warning)
    result.diagnostics = [*reconciliation_result.diagnostics, *result.diagnostics]

    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
