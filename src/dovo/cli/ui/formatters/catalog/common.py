"""Shared base and Rich tables for catalog formatters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel
from rich.table import Table

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.catalog.catalog_views import (
    CatalogItemView,
    CatalogTemplateView,
)
from dovo.core.catalog.models import CatalogItemType, CatalogResult


class CatalogFormatter[T: CatalogResult, V: BaseModel](ComponentFormatter[T, V]):
    """Formatter for a result shared by the blueprint and step commands, named by the result's item type."""

    event_types: ClassVar[Mapping[CatalogItemType, str]]

    def event_type_of(self, data: T) -> str:
        """Return the declared name for the result's item type, e.g. `blueprint.list` or `step.list`."""
        return self.event_types[data.item_type]

    def declared_event_types(self) -> tuple[str, ...]:
        """Return the declared name of every item type."""
        return tuple(self.event_types.values())


def catalog_type_label(item_type: str) -> str:
    """Return the plural label of item_type, e.g. 'Blueprints' for 'blueprint' and 'Steps' for 'step'."""
    return f"{item_type.capitalize()}s"


def build_catalog_table(items: list[CatalogItemView], *, item_type: str) -> Table:
    """Build the Rich table displaying catalog items of one type.

    Args:
        items: List of CatalogItemView instances.
        item_type: The item type listed ('blueprint' or 'step'), used for the table title.

    Returns:
        A Rich Table with Name, Type, Path, SHA columns.
    """
    table = Table(title=f"{catalog_type_label(item_type)}:", title_justify="left", show_header=True)
    table.add_column("Name")
    table.add_column("Type", no_wrap=True)
    table.add_column("Tier", no_wrap=True)
    table.add_column("Path")
    table.add_column("SHA", no_wrap=True)

    for item in items:
        table.add_row(
            item.name,
            item.item_type,
            item.tier,
            item.path,
            item.sha,
        )

    return table


def build_catalog_template_table(templates: list[CatalogTemplateView]) -> Table:
    """Build the Rich table displaying packaged `default.yml` templates.

    Args:
        templates: List of CatalogTemplateView instances.

    Returns:
        A Rich Table with TYPE and PATH columns.
    """
    table = Table(title="Catalog Templates:", title_justify="left", show_header=True)
    table.add_column("TYPE", no_wrap=True)
    table.add_column("PATH")

    for template in templates:
        table.add_row(template.item_type, template.path)

    return table
