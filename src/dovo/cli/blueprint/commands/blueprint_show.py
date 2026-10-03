"""Orchestration logic for ``dovo blueprint show`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType, CatalogShowResult


def blueprint_show_command(
    context: CliContext,
    sha_or_name: str,
    output_format: str = "terminal",
) -> CatalogShowResult:
    """Show details and definition content of a blueprint.

    Args:
        context: CLI context instance.
        sha_or_name: SHA identifier or name of the blueprint.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogShowResult containing record and content or errors.
    """
    result = Catalog(context.paths).show(sha_or_name, item_type=CatalogItemType.BLUEPRINT)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
