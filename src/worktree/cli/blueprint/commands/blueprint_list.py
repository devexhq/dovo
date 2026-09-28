"""Orchestration logic for ``wt blueprint list`` CLI command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.catalog import Catalog
from worktree.core.catalog.models import CatalogItemType, CatalogListResult


def blueprint_list_command(context: CliContext, output_format: str = "terminal") -> CatalogListResult:
    """List blueprint catalog items across all tiers.

    Args:
        context: CLI context instance.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogListResult containing listed records and errors.
    """
    result = Catalog(context.paths).list(type_filter=CatalogItemType.BLUEPRINT)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
