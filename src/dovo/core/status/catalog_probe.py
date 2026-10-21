"""Catalog directory probe for workspace status."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import Filesystem
from dovo.core.status.models import CatalogStatusInfo


def _scan_catalog_category(category_dir: Path) -> tuple[int, int, list[str]]:
    """Scan a catalog category directory, returning (total_count, invalid_count, item_names)."""
    if not category_dir.is_dir():
        return 0, 0, []

    entries = Filesystem.scan_yaml_directory(category_dir)
    invalid_count = 0
    names: list[str] = []

    for entry in entries:
        names.append(entry.name)
        if entry.error is not None or entry.parsed is None or not isinstance(entry.parsed, dict):
            invalid_count += 1

    return len(entries), invalid_count, names


def probe_catalog(catalog_dir: Path) -> CatalogStatusInfo:
    """Count blueprint and step YAML files under catalog_dir and how many fail to parse as a mapping."""
    if not catalog_dir.is_dir():
        return CatalogStatusInfo(
            exists=False,
            catalog_dir=catalog_dir,
            total_items=0,
            blueprints_count=0,
            steps_count=0,
            invalid_items=0,
            item_names=[],
        )

    blueprints_count, invalid_blueprints, blueprint_names = _scan_catalog_category(catalog_dir / "blueprints")
    steps_count, invalid_steps, step_names = _scan_catalog_category(catalog_dir / "steps")

    total_items = blueprints_count + steps_count
    invalid_items = invalid_blueprints + invalid_steps
    item_names = [*blueprint_names, *step_names]

    return CatalogStatusInfo(
        exists=True,
        catalog_dir=catalog_dir,
        total_items=total_items,
        blueprints_count=blueprints_count,
        steps_count=steps_count,
        invalid_items=invalid_items,
        item_names=item_names,
    )
