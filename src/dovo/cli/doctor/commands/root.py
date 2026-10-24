"""Doctor command implementation."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.diagnostics import CheckCategory, Diagnostics, DiagnosticsReport


def doctor_command(
    context: CliContext,
    categories: list[CheckCategory] | None = None,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> DiagnosticsReport:
    """Run registered diagnostic checks and print the doctor report."""
    result = Diagnostics(context.paths, git=context.git).run_diagnostics(categories=categories, config=context.config)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
