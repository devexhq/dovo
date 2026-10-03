"""Shared Rich tables for catalog formatters."""

from __future__ import annotations

from rich.table import Table

from dovo.cli.ui.formatters.catalog.catalog_views import (
    CatalogItemView,
    CatalogTemplateView,
)


def catalog_type_label(type_filter: str | None, items: list[CatalogItemView]) -> str:
    """Derive a plural item-type label (e.g. 'Blueprints', 'Steps') from a type filter or a sample item."""
    resolved = type_filter or (items[0].item_type if items else None)
    return f"{resolved.capitalize()}s" if resolved else "Items"


def build_catalog_table(items: list[CatalogItemView], *, type_filter: str | None = None) -> Table:
    """Build the Rich table displaying catalog items of one type.

    Args:
        items: List of CatalogItemView instances.
        type_filter: The item type the caller filtered on ('blueprint' or 'step'), used for the table title.

    Returns:
        A Rich Table with Name, Type, Path, SHA columns.
    """
    table = Table(title=f"{catalog_type_label(type_filter, items)}:", title_justify="left", show_header=True)
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
