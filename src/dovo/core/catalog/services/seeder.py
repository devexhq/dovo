"""Seed packaged catalog blueprint templates into `.dovo/catalog/<type>s/dovo/`."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.common.utils import display_path
from dovo.core.catalog.models import CatalogItemType, CatalogItemTypeDirectory, SeedResult


def _iter_source_files(source_dir: Path) -> list[Path]:
    """Return sorted list of all files found recursively within a source directory."""
    return sorted(p for p in source_dir.rglob("*") if p.is_file())


def _seed_one_file(source_file: Path, target_path: Path, *, force: bool, result: SeedResult) -> None:
    """Copy a single seed file to target path, respecting force flag and recording status."""
    if target_path.exists() and target_path.is_dir():
        result.errors.append(f"{display_path(target_path)} exists as a directory, not a file.")
        return

    existed = target_path.exists()
    if existed and not force:
        result.skipped_existing_files.append(target_path)
        return

    try:
        text = source_file.read_text(encoding="utf-8")
        Filesystem.atomic_write_text(target_path, text)
    except OSError as exc:
        result.errors.append(f"{display_path(target_path)}: {exc}")
        return

    if existed:
        result.overwritten_files.append(target_path)
    else:
        result.created_files.append(target_path)


def seed_catalog_templates(
    item_type: CatalogItemType,
    paths: WorkspacePaths,
    *,
    force: bool = False,
) -> SeedResult:
    """Copy curated `dovo/` seed files for `item_type` into `.dovo/catalog/<type>/dovo/`."""
    with WorkspaceLock(paths.lock_file):
        result = SeedResult()

        source_dir = paths.catalog_templates_dir / CatalogItemTypeDirectory[item_type.name] / "dovo"
        if not source_dir.is_dir():
            return result

        target_dir = paths.catalog_dir / CatalogItemTypeDirectory[item_type.name] / "dovo"

        for source_file in _iter_source_files(Path(str(source_dir))):
            rel_name = source_file.relative_to(Path(str(source_dir)))
            target_path = target_dir / rel_name
            _seed_one_file(source_file, target_path, force=force, result=result)

        return result


def seed_all_catalog_templates(
    paths: WorkspacePaths,
    *,
    force: bool = False,
) -> SeedResult:
    """Seed curated `dovo/` templates for blueprints, and steps; aggregate the results."""
    with WorkspaceLock(paths.lock_file):
        aggregate = SeedResult()

        for item_type in (
            CatalogItemType.BLUEPRINT,
            CatalogItemType.STEP,
        ):
            result = seed_catalog_templates(item_type, paths, force=force)
            aggregate.created_files.extend(result.created_files)
            aggregate.skipped_existing_files.extend(result.skipped_existing_files)
            aggregate.overwritten_files.extend(result.overwritten_files)
            aggregate.warnings.extend(result.warnings)
            aggregate.errors.extend(result.errors)

        return aggregate
