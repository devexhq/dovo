"""Handles `dovo config set` command."""

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.config import Config
from dovo.core.config.mutate import ConfigSetResult


def config_set_command(
    context: CliContext,
    key: str,
    value: str,
    output_format: str = "terminal",
) -> ConfigSetResult:
    """Set a configuration value by top-level or nested dot-path key.

    String inputs are parsed into native types (bool, int, float, list, dict).
    Missing intermediate objects are created. Type collisions, schema key errors,
    and load/write failures abort without partial writes. Validates keys and values
    against the V1 schema allow-list.

    Args:
        context: CLI context instance.
        key: Dot-path key (e.g. ``agent.model`` or ``version``).
        value: String value from CLI to parse and store at ``key``.
        output_format: Presentation format ("terminal" or "json").
    """
    result = Config(context.paths).set(key, value)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
