"""Catalog blueprint directory scanner and disk-backed multi-tier index services."""

from __future__ import annotations

from importlib.resources.abc import Traversable
from pathlib import Path

from dovo.common.filesystem import Filesystem, WorkspacePaths, YamlFile
from dovo.common.filesystem.services.operations import delete_file
from dovo.common.lock import WorkspaceLock, resolve_lock_file_path
from dovo.core.catalog.exceptions import CatalogProtectionError, CatalogTierDeleteError, CatalogWriteError
from dovo.core.catalog.models import (
    CatalogIndex,
    CatalogIndexEntry,
    CatalogItemType,
    CatalogItemTypeDirectory,
    CatalogRecord,
    CatalogScanResult,
    CatalogSubdirectoryScanResult,
    CatalogTier,
)


def get_catalog_dir(paths: WorkspacePaths) -> Path:
    """Return absolute path to local `.dovo/catalog/` blueprint directory."""
    return paths.catalog_dir


def ensure_tier_catalog_dirs(tier: CatalogTier, paths: WorkspacePaths) -> Path:
    """Ensure one tier's `blueprints`/`steps` subdirectories exist and return that tier's catalog root."""
    tier_dir = tier_root(tier, paths)
    (tier_dir / "blueprints").mkdir(parents=True, exist_ok=True)
    (tier_dir / "steps").mkdir(parents=True, exist_ok=True)
    return tier_dir


def compute_catalog_sha(item_type: CatalogItemType | str, content: str) -> tuple[str, str]:
    """Compute SHA-256 checksum and formatted SHA string (e.g. `blueprint_a1b2c3d`)."""
    type_str = item_type.value if isinstance(item_type, CatalogItemType) else str(item_type)
    checksum = Filesystem.compute_checksum(content)
    sha = f"{type_str}_{checksum[:7]}"
    return sha, checksum


def _catalog_namespace(file_path: Path, item_type_dir: CatalogItemTypeDirectory) -> str | None:
    """Return the directory path below a catalog item-type directory, if any."""
    try:
        type_directory_index = file_path.parts.index(item_type_dir.value)
    except ValueError as exc:
        raise ValueError(f"Catalog file path '{file_path}' is not inside '{item_type_dir.value}'.") from exc

    namespace_parts = file_path.parts[type_directory_index + 1 : -1]
    return "/".join(namespace_parts) if namespace_parts else None


def _catalog_key(namespace: str | None, stem: str) -> str:
    """Derive the catalog lookup key from an optional namespace and the file stem."""
    return f"{namespace}/{stem}" if namespace else stem


def tier_root(tier: CatalogTier, paths: WorkspacePaths) -> Path:
    """Return the disk-backed catalog directory root for one tier (PACKAGED is not disk-backed and is not a valid input)."""
    return paths.catalog_dir_for(tier)


def load_catalog_index(tier_dir: Path) -> CatalogIndex:
    """Read and parse tier_dir/index.json, returning an empty CatalogIndex when the file is absent or invalid."""
    index_path = tier_dir / "index.json"
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return CatalogIndex()

    try:
        return CatalogIndex.model_validate_json(text)
    except ValueError:
        return CatalogIndex()


def write_catalog_index(tier_dir: Path, index: CatalogIndex) -> None:
    """Atomically overwrite tier_dir/index.json with index's full contents."""
    Filesystem.atomic_write_json(tier_dir / "index.json", index.model_dump(mode="json"))


def _index_entry_for_file(
    item_type: CatalogItemType,
    tier_dir: Path,
    file_entry: YamlFile,
) -> tuple[CatalogIndexEntry | None, str | None]:
    """Build one CatalogIndexEntry for a scanned YAML file, or return an error message."""
    if file_entry.error:
        return None, file_entry.error
    sha, checksum = compute_catalog_sha(item_type, str(file_entry.content))
    rel_path = file_entry.path.relative_to(tier_dir)
    namespace = _catalog_namespace(file_entry.path, CatalogItemTypeDirectory[item_type.name])
    entry = CatalogIndexEntry(
        sha=sha,
        key=_catalog_key(namespace, rel_path.stem),
        item_type=item_type,
        name=file_entry.name,
        namespace=namespace,
        path=rel_path,
        checksum=checksum,
    )
    return entry, None


def _append_scan_result(
    result: CatalogSubdirectoryScanResult,
    *,
    entry: CatalogIndexEntry | None,
    error: str | None,
) -> None:
    """Accumulate one scanned catalog entry into the scan result."""
    if error is not None:
        result.errors.append(error)
        return
    if entry is None:
        return
    result.scanned_records.append(entry)
    result.scanned_shas.add(entry.sha)


def _scan_tier_subdirectories(tier_dir: Path) -> CatalogSubdirectoryScanResult:
    """Scan one tier's blueprints/steps subdirectories for YAML files and index each entry."""
    result = CatalogSubdirectoryScanResult(scanned_records=[], errors=[], scanned_shas=set())

    subdirs: list[tuple[CatalogItemType, Path]] = [
        (CatalogItemType.BLUEPRINT, tier_dir / "blueprints"),
        (CatalogItemType.STEP, tier_dir / "steps"),
    ]
    for item_type, sub_dir in subdirs:
        if not sub_dir.exists():
            continue
        for file_entry in Filesystem.scan_yaml_directory(sub_dir):
            entry, error = _index_entry_for_file(item_type, tier_dir, file_entry)
            _append_scan_result(result, entry=entry, error=error)

    return result


def _global_tier_lock_path(paths: WorkspacePaths) -> Path:
    """Return the lock path used for GLOBAL/USER tier catalog mutations."""
    return resolve_lock_file_path(paths.global_paths.data_dir)


def scan_and_index_tier(tier: CatalogTier, paths: WorkspacePaths) -> CatalogScanResult:
    """Walk one tier's blueprints/steps directories, compute SHAs, and rewrite that tier's index.json wholesale."""
    tier_dir = tier_root(tier, paths)
    tier_dir.mkdir(parents=True, exist_ok=True)

    lock_path = paths.lock_file if tier is CatalogTier.REPO else _global_tier_lock_path(paths)
    with WorkspaceLock(lock_path):
        scan_result = _scan_tier_subdirectories(tier_dir)
        write_catalog_index(tier_dir, CatalogIndex(items=scan_result.scanned_records))

        records = [CatalogRecord(**entry.model_dump(), tier=tier) for entry in scan_result.scanned_records]
        return CatalogScanResult(items=records, errors=scan_result.errors)


def scan_and_index_catalog(paths: WorkspacePaths) -> CatalogScanResult:
    """Scan and reindex the GLOBAL, USER, and REPO catalog tiers in precedence order, aggregating warnings."""
    items: list[CatalogRecord] = []
    errors: list[str] = []
    for tier in (CatalogTier.GLOBAL, CatalogTier.USER, CatalogTier.REPO):
        result = scan_and_index_tier(tier, paths)
        items.extend(result.items)
        errors.extend(result.errors)

    return CatalogScanResult(items=items, errors=errors)


def list_packaged_template_defaults(catalog_templates_dir: Traversable) -> list[tuple[str, str]]:
    """Return (type, relative_path) pairs for the packaged `default.yml` templates."""
    root = catalog_templates_dir
    rows: list[tuple[str, str]] = []
    for item_type in (CatalogItemType.BLUEPRINT, CatalogItemType.STEP):
        rel_path = f"{item_type.value}s/default.yml"
        if (root / rel_path).is_file():
            rows.append((item_type.value, rel_path))
    return rows


def find_packaged_templates(sha_or_name: str, catalog_templates_dir: Traversable) -> list[tuple[str, str]]:
    """Return (relative_path, content) pairs for packaged templates matching `sha_or_name`."""
    root = catalog_templates_dir
    clean_name = sha_or_name.removeprefix("dovo/")
    found: list[tuple[str, str]] = []
    for type_dir in ("blueprints", "steps"):
        candidate = (
            (root / type_dir / "default.yml")
            if clean_name == "default"
            else (root / type_dir / "dovo" / f"{clean_name}.yml")
        )
        if candidate.is_file():
            rel_path = f"{type_dir}/default.yml" if clean_name == "default" else f"{type_dir}/dovo/{clean_name}.yml"
            found.append((rel_path, candidate.read_text(encoding="utf-8")))
    return found


def _packaged_default_records(catalog_templates_dir: Traversable) -> list[CatalogRecord]:
    """Build CatalogRecord entries for the packaged `default.yml` starter templates."""
    root = catalog_templates_dir
    records: list[CatalogRecord] = []
    for item_type_value, rel_path in list_packaged_template_defaults(catalog_templates_dir):
        item_type = CatalogItemType(item_type_value)
        path = Path(rel_path)
        content = (root / rel_path).read_text(encoding="utf-8")
        sha, checksum = compute_catalog_sha(item_type, content)
        records.append(
            CatalogRecord(
                sha=sha,
                key=path.stem,
                item_type=item_type,
                name=path.stem,
                namespace=None,
                path=path,
                checksum=checksum,
                tier=CatalogTier.PACKAGED,
            )
        )
    return records


def resolve_catalog_records(paths: WorkspacePaths) -> list[CatalogRecord]:
    """Return every indexed record across all four tiers, ordered REPO, USER, GLOBAL, PACKAGED (most specific first)."""
    records: list[CatalogRecord] = []
    for tier in (CatalogTier.REPO, CatalogTier.USER, CatalogTier.GLOBAL):
        tier_dir = tier_root(tier, paths)
        index = load_catalog_index(tier_dir)
        records.extend(CatalogRecord(**entry.model_dump(), tier=tier) for entry in index.items)

    records.extend(_packaged_default_records(paths.catalog_templates_dir))
    return records


def _resolve_packaged_dovo_fallback(
    key_or_sha: str, item_type: CatalogItemType | None, catalog_templates_dir: Traversable
) -> CatalogRecord | None:
    """Resolve a `dovo/`-namespaced packaged example by name, mirroring the pre-pivot lookup path."""
    for rel_path, content in find_packaged_templates(key_or_sha, catalog_templates_dir):
        path = Path(rel_path)
        type_enum = CatalogItemType.BLUEPRINT if path.parts[0] == "blueprints" else CatalogItemType.STEP
        if item_type is not None and type_enum != item_type:
            continue
        sha, checksum = compute_catalog_sha(type_enum, content)
        namespace = _catalog_namespace(path, CatalogItemTypeDirectory[type_enum.name])
        return CatalogRecord(
            sha=sha,
            key=_catalog_key(namespace, path.stem),
            item_type=type_enum,
            name=path.stem,
            namespace=namespace,
            path=path,
            checksum=checksum,
            tier=CatalogTier.PACKAGED,
        )
    return None


def find_catalog_record_matches(
    key_or_sha: str,
    item_type: CatalogItemType | None,
    paths: WorkspacePaths,
) -> list[CatalogRecord]:
    """Return every CatalogRecord matching key_or_sha across tiers, in REPO, USER, GLOBAL, PACKAGED order."""
    records = resolve_catalog_records(paths)
    if item_type is not None:
        records = [record for record in records if record.item_type == item_type]

    matches = [record for record in records if record.key == key_or_sha or record.sha == key_or_sha]
    if matches:
        return matches

    fallback = _resolve_packaged_dovo_fallback(key_or_sha, item_type, paths.catalog_templates_dir)
    return [fallback] if fallback is not None else []


def get_catalog_record(
    key_or_sha: str,
    item_type: CatalogItemType | None,
    paths: WorkspacePaths,
) -> CatalogRecord | None:
    """Return the first CatalogRecord matching key_or_sha across tiers in REPO, USER, GLOBAL, PACKAGED order."""
    matches = find_catalog_record_matches(key_or_sha, item_type, paths)
    return matches[0] if matches else None


def resolve_catalog_record_path(record: CatalogRecord, paths: WorkspacePaths) -> Path:
    """Return the absolute file path for an indexed record, resolved against its own tier's root."""
    if record.tier == CatalogTier.PACKAGED:
        return Path(str(paths.catalog_templates_dir)) / record.path
    return tier_root(record.tier, paths) / record.path


def _get_initial_template_content(type_enum: CatalogItemType, stem: str, catalog_templates_dir: Traversable) -> str:
    """Return initial template text for a catalog item or fall back to a default skeleton."""
    template_path = catalog_templates_dir / f"{type_enum.value}s" / "default.yml"
    try:
        content = template_path.read_text(encoding="utf-8")
        placeholder = "my-step" if type_enum == CatalogItemType.STEP else "my-blueprint"
        return content.replace(placeholder, stem)
    except Exception:
        # Defensive fallback if the packaged resource is unreadable
        if type_enum == CatalogItemType.BLUEPRINT:
            return f'version: "1.0"\nname: {stem}\ndescription: Custom blueprint\nsteps: []\n'
        return f"name: {stem}\ndescription: Custom step blueprint\naction: run\n"


def coerce_catalog_item_type(item_type: CatalogItemType | str) -> CatalogItemType:
    """Parse a catalog item type, or raise ValueError with allowed choices."""
    try:
        return item_type if isinstance(item_type, CatalogItemType) else CatalogItemType(str(item_type).lower())
    except ValueError as exc:
        allowed = ", ".join([t.value for t in CatalogItemType])
        raise ValueError(f"Invalid item_type '{item_type}'. Allowed choices: {allowed}") from exc


def create_catalog_item(
    item_type: CatalogItemType | str,
    name: str,
    tier: CatalogTier,
    paths: WorkspacePaths,
) -> CatalogRecord:
    """Create a new catalog item under the selected tier's `<type>s/<name>.yml` and reindex that tier."""
    lock_path = paths.lock_file if tier is CatalogTier.REPO else _global_tier_lock_path(paths)
    with WorkspaceLock(lock_path):
        type_enum = coerce_catalog_item_type(item_type)
        tier_dir = ensure_tier_catalog_dirs(tier, paths)
        stem = name[:-4] if name.endswith(".yml") or name.endswith(".yaml") else name
        filename = f"{stem}.yml"
        target_path = tier_dir / f"{type_enum.value}s" / filename

        if target_path.exists():
            rel_path = target_path.relative_to(tier_dir)
            raise FileExistsError(f"Catalog blueprint collision at path '{rel_path}'")

        content = _get_initial_template_content(type_enum, stem, paths.catalog_templates_dir)
        Filesystem.atomic_write_text(target_path, content)

        scan_result = scan_and_index_tier(tier, paths)
        rel_path = target_path.relative_to(tier_dir)
        record = next((r for r in scan_result.items if r.path == rel_path), None)
        if record is None:
            raise CatalogWriteError(f"Failed to reindex newly created catalog blueprint '{rel_path}'.")
        return record


def delete_catalog_item_by_sha_or_name(sha_or_name: str, paths: WorkspacePaths) -> CatalogRecord | None:
    """Delete a REPO-tier catalog file and reindex.

    Raises:
        CatalogTierDeleteError: If the resolved match is not indexed at the REPO tier.
        CatalogProtectionError: If attempting to delete a template in the protected 'dovo/' namespace.
    """
    if sha_or_name.startswith("dovo/"):
        raise CatalogProtectionError(f"Cannot delete bundled catalog template '{sha_or_name}'.")

    catalog_dir = paths.catalog_dir
    with WorkspaceLock(paths.lock_file):
        scan_and_index_catalog(paths)
        matches = find_catalog_record_matches(sha_or_name, None, paths)
        if not matches:
            return None

        item = matches[0]
        if item.tier != CatalogTier.REPO:
            raise CatalogTierDeleteError(
                f"Catalog item '{item.key}' resolved from tier '{item.tier.value}'; only repo-tier items can be deleted."
            )

        if item.namespace == "dovo":
            raise CatalogProtectionError(f"Cannot delete bundled catalog template '{item.key}'.")

        file_path = catalog_dir / item.path
        delete_file(file_path)

        scan_and_index_tier(CatalogTier.REPO, paths)
        return item
