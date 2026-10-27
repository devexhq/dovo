"""Orchestration logic for ``dovo step delete`` CLI command."""

from __future__ import annotations

from collections.abc import Callable

import typer

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogDeleteResult, CatalogRecord


def _confirm_delete(sha_or_name: str) -> Callable[[CatalogRecord], bool]:
    """Return a confirm callback asking to delete step sha_or_name; it returns False when declined or aborted."""

    def confirm(_item: CatalogRecord) -> bool:
        try:
            return typer.confirm(
                f"Are you sure you want to delete step '{sha_or_name}'?",
                default=False,
            )
        except typer.Abort:
            return False

    return confirm


def step_delete_command(
    context: CliContext,
    sha_or_name: str,
    force: bool = False,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> CatalogDeleteResult:
    """Delete a repo-tier step file and reindex, confirming first unless force.

    Args:
        context: CLI context instance.
        sha_or_name: SHA identifier or name of the step to delete.
        force: When True, skip the confirmation prompt.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogDeleteResult: the deleted item, a declined confirmation, or the failure.
    """
    result = Catalog(context.paths).delete(sha_or_name, confirm=None if force else _confirm_delete(sha_or_name))
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
