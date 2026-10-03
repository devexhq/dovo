"""Orchestration logic for ``dovo step create`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogCreateResult, CatalogItemType, CatalogTier


def _resolve_create_tier(*, user: bool, global_: bool) -> CatalogTier | CatalogCreateResult:
    """Coerce --user/--global flags into a CatalogTier, or a mutual-exclusion error result."""
    if user and global_:
        return CatalogCreateResult(errors=["--user and --global are mutually exclusive."])
    if user:
        return CatalogTier.USER
    if global_:
        return CatalogTier.GLOBAL
    return CatalogTier.REPO


def step_create_command(
    context: CliContext,
    name: str,
    user: bool = False,
    global_: bool = False,
    output_format: str = "terminal",
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
    tier_or_error = _resolve_create_tier(user=user, global_=global_)
    if isinstance(tier_or_error, CatalogCreateResult):
        ui_dispatcher.dispatch(tier_or_error, output_format=output_format)
        return tier_or_error

    result = Catalog(context.paths).create(item_type=CatalogItemType.STEP, name=name, tier=tier_or_error)
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
