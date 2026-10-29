"""Seed packaged catalog blueprint templates into `.dovo/catalog/<type>s/dovo/`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.common.utils import display_path
from dovo.core.catalog.exceptions import CatalogError
from dovo.core.catalog.models import CatalogItemType, CatalogItemTypeDirectory, SeedSummary


@dataclass(slots=True)
class _SeedTally:
    """Files seeded so far, each failed file's message, and the first OSError among the failures."""

    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    overwritten: list[Path] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    first_os_error: OSError | None = None

    def merge(self, other: _SeedTally) -> None:
        """Append other's files and failures, keeping the first OSError seen."""
        self.created.extend(other.created)
        self.skipped.extend(other.skipped)
        self.overwritten.extend(other.overwritten)
        self.failures.extend(other.failures)
        self.first_os_error = self.first_os_error or other.first_os_error


def _iter_source_files(source_dir: Path) -> list[Path]:
    """Return sorted list of all files found recursively within a source directory."""
    return sorted(p for p in source_dir.rglob("*") if p.is_file())


def _seed_one_file(source_file: Path, target_path: Path, *, force: bool, tally: _SeedTally) -> None:
    """Copy a single seed file to target path, respecting force flag and recording its outcome in tally."""
    if target_path.exists() and target_path.is_dir():
        tally.failures.append(f"{display_path(target_path)} exists as a directory, not a file.")
        return

    existed = target_path.exists()
    if existed and not force:
        tally.skipped.append(target_path)
        return

    try:
        text = source_file.read_text(encoding="utf-8")
        Filesystem.atomic_write_text(target_path, text)
    except OSError as exc:
        tally.failures.append(f"{display_path(target_path)}: {exc}")
        tally.first_os_error = tally.first_os_error or exc
        return

    if existed:
        tally.overwritten.append(target_path)
    else:
        tally.created.append(target_path)


def _seed_files(item_type: CatalogItemType, paths: WorkspacePaths, *, force: bool) -> _SeedTally:
    """Copy every curated `dovo/` seed file of item_type, recording each file's outcome rather than stopping at a failure."""
    tally = _SeedTally()
    source_dir = paths.catalog_templates_dir / CatalogItemTypeDirectory[item_type.name] / "dovo"
    if not source_dir.is_dir():
        return tally

    target_dir = paths.catalog_dir / CatalogItemTypeDirectory[item_type.name] / "dovo"
    for source_file in _iter_source_files(Path(str(source_dir))):
        rel_name = source_file.relative_to(Path(str(source_dir)))
        _seed_one_file(source_file, target_dir / rel_name, force=force, tally=tally)
    return tally


def _summarize(tally: _SeedTally) -> SeedSummary:
    """Return tally as a SeedSummary, or raise one CatalogError listing every failed file."""
    if tally.failures:
        message = "\n".join(["Could not seed the starter catalog templates:", *tally.failures])
        raise CatalogError(ErrorCode.CATALOG_SEED_FAILED, message) from tally.first_os_error
    return SeedSummary(
        created_files=tuple(tally.created),
        skipped_existing_files=tuple(tally.skipped),
        overwritten_files=tuple(tally.overwritten),
    )


def seed_catalog_templates(
    item_type: CatalogItemType,
    paths: WorkspacePaths,
    *,
    force: bool = False,
) -> SeedSummary:
    """Copy curated `dovo/` seed files for `item_type` into `.dovo/catalog/<type>/dovo/`.

    Raises:
        CatalogError: CATALOG_SEED_FAILED, after trying every file, when any target is a directory or cannot be written.
    """
    with WorkspaceLock(paths.lock_file):
        tally = _seed_files(item_type, paths, force=force)
    return _summarize(tally)


def seed_all_catalog_templates(
    paths: WorkspacePaths,
    *,
    force: bool = False,
) -> SeedSummary:
    """Seed curated `dovo/` templates for blueprints, then steps, into one summary.

    Raises:
        CatalogError: CATALOG_SEED_FAILED, after trying every file of both types, when any target is a directory or
            cannot be written.
    """
    tally = _SeedTally()
    with WorkspaceLock(paths.lock_file):
        for item_type in (CatalogItemType.BLUEPRINT, CatalogItemType.STEP):
            tally.merge(_seed_files(item_type, paths, force=force))
    return _summarize(tally)
