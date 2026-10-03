"""ComponentFormatter for CatalogListResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.catalog.catalog_views import (
    CatalogItemView,
    CatalogListView,
    CatalogTemplateView,
)
from dovo.cli.ui.formatters.catalog.common import (
    build_catalog_table,
    build_catalog_template_table,
    catalog_type_label,
)
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.common.utils import enum_value
from dovo.core.catalog.models import CatalogListResult


def _render_list_empty(view: CatalogListView) -> Any:
    """Render empty state message and any warnings for catalog list."""
    label = catalog_type_label(view.type_filter, view.items).lower()
    renderables: list[Any] = [Text(f"No {label} found.")]
    for warning in view.warnings:
        renderables.append(Panel(warning, title="Catalog Scan Warning", border_style="red"))
    return Group(*renderables) if view.warnings else renderables[0]


def _render_list_items(view: CatalogListView) -> Any:
    """Render table of catalog items along with any warnings."""
    table = build_catalog_table(view.items, type_filter=view.type_filter)
    if not view.warnings:
        return table
    renderables: list[Any] = [table]
    for warning in view.warnings:
        renderables.append(Panel(warning, title="Catalog Scan Warning", border_style="red"))
    return Group(*renderables)


class CatalogListFormatter(ComponentFormatter[CatalogListResult, CatalogListView]):
    """Formatter for catalog list command results."""

    def transform(self, data: CatalogListResult) -> CatalogListView:
        """Derive the presentation-ready view from CatalogListResult.

        Args:
            data: Domain CatalogListResult.

        Returns:
            CatalogListView with mapped item and template models.
        """
        items = [
            CatalogItemView(
                sha=item.sha,
                item_type=enum_value(item.item_type),
                name=item.name,
                path=item.path.as_posix(),
                checksum=item.checksum,
                tier=enum_value(item.tier),
            )
            for item in data.items
        ]
        templates = [
            CatalogTemplateView(item_type=template_type, path=template_path)
            for template_type, template_path in data.templates
        ]
        return CatalogListView(
            items=items,
            type_filter=enum_value(data.type_filter) if data.type_filter else None,
            templates=templates,
            total_items=len(data.items),
            errors=data.errors,
            warnings=data.warnings,
            fixes=data.fixes,
        )

    def to_rich(self, data: CatalogListResult) -> Any:
        """Render catalog blueprint list, templates table, or empty state."""
        view = self.transform(data)

        if view.errors:
            return build_error_panel("Catalog Filter Error", view.errors, fixes=view.fixes)

        if view.templates:
            return build_catalog_template_table(view.templates)

        if view.type_filter == "template":
            return Text("No packaged templates found.")

        if not view.items:
            return _render_list_empty(view)

        return _render_list_items(view)
