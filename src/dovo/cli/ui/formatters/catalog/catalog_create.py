"""ComponentFormatter for CatalogCreateResult."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.catalog.catalog_views import CatalogCreateView
from dovo.cli.ui.formatters.catalog.common import CatalogFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.common.utils import enum_value
from dovo.core.catalog.models import CatalogCreateResult, CatalogItemType, CatalogTier


def _display_path(tier: CatalogTier, rel_path: Path, resolved_path: Path) -> str:
    """Return the repo-relative display path for REPO tier, or the plain absolute path otherwise.

    Kept out of transform(): the view is also the JSON payload, so a display path stored there would overwrite the
    raw absolute `resolved_path` JSON must keep.
    """
    if tier == CatalogTier.REPO:
        return (Path(".dovo") / "catalog" / rel_path).as_posix()
    return str(resolved_path)


def _render(view: CatalogCreateView) -> RenderableType:
    """Render blueprint creation confirmation or failure panel."""
    if view.errors or not view.ok or view.item is None or view.resolved_path is None:
        return build_error_panel(
            "Catalog Creation Failed",
            view.errors,
            "Catalog creation failed.",
            remediations=view.remediations,
        )

    item_type = enum_value(view.item.item_type)
    tier = enum_value(view.item.tier)
    path = _display_path(view.item.tier, view.item.path, view.resolved_path)
    return Text(f"Created {item_type} '{view.item.name}' (tier: {tier}) at '{path}'.")


class CatalogCreateFormatter(CatalogFormatter[CatalogCreateResult, CatalogCreateView]):
    """Formatter for catalog create command results."""

    event_types: ClassVar[Mapping[CatalogItemType, str]] = {
        CatalogItemType.BLUEPRINT: "blueprint.create",
        CatalogItemType.STEP: "step.create",
    }

    def transform(self, data: CatalogCreateResult) -> CatalogCreateView:
        """Derive the view: the result header, then the created item and its resolved path."""
        return CatalogCreateView(
            **header_fields(data),
            item_type=data.item_type,
            item=data.item,
            resolved_path=data.resolved_path,
        )

    def to_rich(self, data: CatalogCreateResult) -> RenderableType:
        """Render blueprint creation confirmation or failure panel, followed by diagnostic_lines(diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_render(view), view.diagnostics)
