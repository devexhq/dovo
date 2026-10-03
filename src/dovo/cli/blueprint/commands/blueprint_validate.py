"""Orchestration logic for ``dovo blueprint validate`` CLI command."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition, StepDefinition
from dovo.core.catalog.models import CatalogItemType, CatalogValidateResult


def blueprint_validate_command(
    context: CliContext,
    target: str,
    output_format: str = "terminal",
) -> CatalogValidateResult:
    """Validate a blueprint definition without executing it.

    Args:
        context: CLI context instance.
        target: Blueprint name, namespaced identifier, or file path to validate.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogValidateResult containing status, resolved path, errors, and warnings.
    """
    result = Catalog(context.paths).validate(
        target,
        item_type=CatalogItemType.BLUEPRINT,
        blueprint_cls=BlueprintDefinition,
        step_cls=StepDefinition,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
