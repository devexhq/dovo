"""Handles `wt config validate` command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.config import Config
from worktree.core.config.validate import ConfigValidationResult


def config_validate_command(
    context: CliContext,
    output_format: str = "terminal",
) -> ConfigValidationResult:
    """Validate config and print the CLI validation report.

    Args:
        context: CLI context instance.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        ConfigValidationResult containing validation results and errors.
    """
    result = Config(context.paths).validate()
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
