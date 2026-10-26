"""Status command implementation."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
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
    result = Status(context.paths, git=context.git, sessions_db=context.db.sessions).collect()
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
