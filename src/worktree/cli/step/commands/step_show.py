"""Orchestration logic for ``wt step show`` CLI command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.catalog import Catalog
from worktree.core.catalog.models import CatalogItemType, CatalogShowResult


def step_show_command(
    context: CliContext,
    sha_or_name: str,
    output_format: str = "terminal",
) -> CatalogShowResult:
    """Show details and definition content of a step.

    Args:
        context: CLI context instance.
        sha_or_name: SHA identifier or name of the step.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogShowResult containing record and content or errors.
    """
    result = Catalog(context.paths).show(sha_or_name, item_type=CatalogItemType.STEP)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
