"""Orchestration logic for ``dovo blueprint create`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogCreateResult, CatalogItemType, CatalogTier


def _resolve_create_tier(*, user: bool, global_: bool) -> CatalogTier:
    """Coerce the --user/--global flags, which the handler has already checked are not both set, into a CatalogTier."""
    if user:
        return CatalogTier.USER
    if global_:
        return CatalogTier.GLOBAL
    return CatalogTier.REPO


def blueprint_create_command(
    context: CliContext,
    name: str,
    user: bool = False,
    global_: bool = False,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> CatalogCreateResult:
    """Create a new blueprint file at the selected tier and reindex.

    Args:
        context: CLI context instance.
        name: Blueprint name.
        user: When True, create at the USER tier.
        global_: When True, create at the GLOBAL tier.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogCreateResult containing created record or errors.
    """
    tier = _resolve_create_tier(user=user, global_=global_)
    result = Catalog(context.paths).create(item_type=CatalogItemType.BLUEPRINT, name=name, tier=tier)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
