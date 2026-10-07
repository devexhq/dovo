# tests/harness/catalog.py
"""Shared test harness for writing a real, indexed catalog blueprint to disk."""

from __future__ import annotations

from pathlib import Path

import yaml

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog.models import CatalogTier
from dovo.core.catalog.services.inventory import ensure_tier_catalog_dirs, scan_and_index_catalog
from tests.harness.workspace_paths import initialized_workspace_paths


def _workspace_paths(workspace: Path) -> WorkspacePaths:
    return initialized_workspace_paths(workspace)


def write_runnable_blueprint(
    workspace: Path,
    *,
    key: str,
    steps: list[dict[str, object]],
    timeout_seconds: int = 60,
) -> None:
    """Write a minimal blueprint YAML under .dovo/catalog/blueprints/ and index it into the catalog DB."""
    paths = _workspace_paths(workspace)
    catalog_dir = ensure_tier_catalog_dirs(CatalogTier.REPO, paths)
    blueprint_path = catalog_dir / "blueprints" / f"{key}.yml"
    payload = {
        "version": "1.0",
        "name": key,
        "id": key,
        "description": f"Test blueprint '{key}'.",
        "timeout_seconds": timeout_seconds,
        "steps": steps,
    }
    blueprint_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    scan_and_index_catalog(paths)


def write_runnable_step(
    workspace: Path,
    *,
    key: str,
    definition: dict[str, object],
) -> None:
    """Write a minimal step YAML under .dovo/catalog/steps/ and index it into the catalog DB."""
    paths = _workspace_paths(workspace)
    catalog_dir = ensure_tier_catalog_dirs(CatalogTier.REPO, paths)
    step_path = catalog_dir / "steps" / f"{key}.yml"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    scan_and_index_catalog(paths)
