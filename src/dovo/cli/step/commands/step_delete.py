"""Orchestration logic for ``dovo step delete`` CLI command."""

from __future__ import annotations

import typer

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogDeleteResult


def _confirm_delete(sha_or_name: str) -> bool:
    """Prompt user for deletion confirmation."""
    try:
        return typer.confirm(
            f"Are you sure you want to delete step '{sha_or_name}'?",
            default=False,
        )
    except typer.Abort:
        return False


def step_delete_command(
    context: CliContext,
    sha_or_name: str,
    force: bool = False,
    output_format: str = "terminal",
) -> CatalogDeleteResult:
    """Delete a repo-tier step file and reindex.

    Args:
        context: CLI context instance.
        sha_or_name: SHA identifier or name of the step to delete.
        force: When True, skip the confirmation prompt.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogDeleteResult indicating deletion status.
    """
    if not force and not _confirm_delete(sha_or_name):
        result = CatalogDeleteResult(cancelled=True, errors=["Deletion cancelled."])
        ui_dispatcher.dispatch(result, output_format=output_format)
        return result

    result = Catalog(context.paths).delete(sha_or_name)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
