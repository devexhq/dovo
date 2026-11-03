"""Orchestration logic for ``dovo step create`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogCreateResult, CatalogItemType


def step_create_command(
    context: CliContext,
    name: str,
    user: bool = False,
    global_: bool = False,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> CatalogCreateResult:
    """Create a new step file at the selected tier and reindex.

    Args:
        context: CLI context instance.
        name: Step name.
        user: When True, create at the USER tier.
        global_: When True, create at the GLOBAL tier.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogCreateResult containing created record or errors.
    """
    result = Catalog(context.paths).create(CatalogItemType.STEP, name, user=user, global_=global_)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
