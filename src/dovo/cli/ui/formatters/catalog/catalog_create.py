"""ComponentFormatter for CatalogCreateResult."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.common.utils import enum_value
from dovo.core.catalog.models import CatalogCreateResult, CatalogTier


def _display_path(tier: CatalogTier, rel_path: Path, resolved_path: Path) -> str:
    """Return the repo-relative display path for REPO tier, or the plain absolute path otherwise.

    Kept out of transform(): this formatter's transform is identity, so whatever it returned would
    also become the JSON payload's `resolved_path`, overwriting the raw absolute path JSON must keep.
    """
    if tier == CatalogTier.REPO:
        return (Path(".dovo") / "catalog" / rel_path).as_posix()
    return str(resolved_path)


class CatalogCreateFormatter(ComponentFormatter[CatalogCreateResult]):
    """Formatter for catalog create command results."""

    def to_rich(self, data: CatalogCreateResult) -> Any:
        """Render blueprint creation confirmation or failure panel."""
        if data.errors or not data.ok or data.item is None or data.resolved_path is None:
            return build_error_panel(
                "Catalog Creation Failed",
                data.errors,
                "Catalog creation failed.",
                data.fixes,
            )

        item_type = enum_value(data.item.item_type)
        tier = enum_value(data.item.tier)
        path = _display_path(data.item.tier, data.item.path, data.resolved_path)
        return Text(f"Created {item_type} '{data.item.name}' (tier: {tier}) at '{path}'.")
