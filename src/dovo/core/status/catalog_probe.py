"""Catalog directory probe for workspace status."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import yaml

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, record_exception
from dovo.core.status.models import CatalogStatusInfo

_YAML_SUFFIXES: Final = (".yml", ".yaml")


def _read_catalog_file(file_path: Path) -> tuple[str, bool, DiagnosticRecord | None]:
    """Return file_path's item name, whether it parses as a YAML mapping, and the error-level record of a failed read.

    The name is the mapping's `name`, else the file stem.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        record = record_exception(
            exc, level=DiagnosticLevel.ERROR, operation="status.catalog", context={"path": str(file_path)}
        )
        return file_path.stem, False, record

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError:
        return file_path.stem, False, None
    if not isinstance(parsed, dict):
        return file_path.stem, False, None
    name = parsed.get("name")
    return (str(name) if name else file_path.stem), True, None


def _scan_catalog_category(category_dir: Path) -> tuple[int, int, list[str], list[DiagnosticRecord]]:
    """Scan a catalog category directory, returning (total_count, invalid_count, item_names, read_failure_records)."""
    if not category_dir.is_dir():
        return 0, 0, [], []

    files = [
        path for path in sorted(category_dir.rglob("*")) if path.is_file() and path.suffix.lower() in _YAML_SUFFIXES
    ]
    invalid_count = 0
    names: list[str] = []
    records: list[DiagnosticRecord] = []

    for file_path in files:
        name, is_valid, record = _read_catalog_file(file_path)
        names.append(name)
        if not is_valid:
            invalid_count += 1
        if record is not None:
            records.append(record)

    return len(files), invalid_count, names, records


def probe_catalog(catalog_dir: Path) -> CatalogStatusInfo:
    """Count blueprint and step YAML files under catalog_dir and how many fail to parse as a mapping.

    A file that cannot be read counts as invalid and is recorded at error level, its record carried on the info.
    """
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

    blueprints_count, invalid_blueprints, blueprint_names, blueprint_records = _scan_catalog_category(
        catalog_dir / "blueprints"
    )
    steps_count, invalid_steps, step_names, step_records = _scan_catalog_category(catalog_dir / "steps")

    return CatalogStatusInfo(
        exists=True,
        catalog_dir=catalog_dir,
        total_items=blueprints_count + steps_count,
        blueprints_count=blueprints_count,
        steps_count=steps_count,
        invalid_items=invalid_blueprints + invalid_steps,
        item_names=[*blueprint_names, *step_names],
        diagnostics=[*blueprint_records, *step_records],
    )
