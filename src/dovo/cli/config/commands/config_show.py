"""Handles `dovo config show` command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.config import Config
from dovo.core.config.loader import ConfigLoadResult


def config_show_command(
    context: CliContext,
    output_format: str = "terminal",
) -> ConfigLoadResult:
    """Print source metadata, then the effective configuration as pretty JSON.

    Args:
        context: CLI context instance.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        ConfigLoadResult containing loaded config and errors.
    """
    result = Config(context.paths).load()
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
