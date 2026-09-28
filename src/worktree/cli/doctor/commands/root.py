"""Doctor command implementation."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.doctor import CheckCategory, Doctor, DoctorReport


def doctor_command(
    context: CliContext,
    categories: list[CheckCategory] | None = None,
    output_format: str = "terminal",
) -> DoctorReport:
    """Run registered diagnostic checks and print the doctor report."""
    result = Doctor(context.paths).run_diagnostics(categories=categories, config=context.config)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
