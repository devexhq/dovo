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
    """Remove a configuration value by top-level or nested dot-path key.

    Falls back to the schema default for the removed key. Removing a key that is
    already absent is a no-op that still exits successfully without writing to disk.

    Args:
        context: CLI context instance.
        key: Dot-path key (e.g. ``agent.model`` or ``version``).
        output_format: Presentation format ("terminal" or "json").
    """
    result = Config(context.paths).unset(key)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
