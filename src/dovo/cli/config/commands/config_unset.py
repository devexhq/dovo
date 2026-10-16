"""Handles `dovo config unset` command."""

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.config import Config
from dovo.core.config.mutate import ConfigUnsetResult


def config_unset_command(
    context: CliContext,
    key: str,
    output_format: str = "terminal",
) -> ConfigUnsetResult:
    """Remove a configuration value by top-level or nested dot-path key."""
    result = Config(context.paths).unset(key)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
