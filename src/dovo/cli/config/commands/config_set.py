"""Handles `dovo config set` command."""

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.config import Config
from dovo.core.config.mutate import ConfigSetResult


def config_set_command(
    context: CliContext,
    key: str,
    value: str,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> ConfigSetResult:
    """Set a configuration value by top-level or nested dot-path key."""
    result = Config(context.paths).set(key, value)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
