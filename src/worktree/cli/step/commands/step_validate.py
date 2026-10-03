"""Orchestration logic for ``wt step validate`` CLI command."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.catalog import Catalog
from worktree.core.catalog.definitions import BlueprintDefinition, StepDefinition
from worktree.core.catalog.models import CatalogItemType, CatalogValidateResult


def step_validate_command(
    context: CliContext,
    target: str,
    output_format: str = "terminal",
) -> CatalogValidateResult:
    """Validate a step definition without executing it.

    Args:
        context: CLI context instance.
        target: Step name, namespaced identifier, or file path to validate.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        CatalogValidateResult containing status, resolved path, errors, and warnings.
    """
    result = Catalog(context.paths).validate(
        target,
        item_type=CatalogItemType.STEP,
        blueprint_cls=BlueprintDefinition,
        step_cls=StepDefinition,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
