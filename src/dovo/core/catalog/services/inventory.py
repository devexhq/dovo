"""Catalog blueprint directory scanner and disk-backed multi-tier index services."""

from __future__ import annotations

from importlib.resources.abc import Traversable
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths, YamlFile
from dovo.common.filesystem.services.operations import delete_file
from dovo.common.lock import WorkspaceLock, resolve_lock_file_path
from dovo.core.catalog.exceptions import CatalogError
from dovo.core.catalog.models import (
    CatalogIndex,
    CatalogIndexEntry,
    CatalogItemType,
    CatalogItemTypeDirectory,
    CatalogRecord,
    CatalogScan,
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
    """Read and parse tier_dir/index.json, returning an empty CatalogIndex when the file is absent or invalid.

    An unreadable or invalid index is recorded at debug level before the empty index is returned.
    """
    index_path = tier_dir / "index.json"
    if not index_path.exists():
        return CatalogIndex()
    try:
        return CatalogIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        record_exception(
            exc,
            level=DiagnosticLevel.DEBUG,
            operation="catalog.read_index",
            context={"path": str(index_path)},
        )
        return CatalogIndex()


def write_catalog_index(tier_dir: Path, index: CatalogIndex) -> None:
    """Atomically overwrite tier_dir/index.json with index's full contents.

    Raises:
        CatalogError: CATALOG_WRITE_FAILED if the index cannot be written.
    """
    index_path = tier_dir / "index.json"
    try:
        Filesystem.atomic_write_json(index_path, index.model_dump(mode="json"))
    except OSError as exc:
        raise CatalogError(
            ErrorCode.CATALOG_WRITE_FAILED, f"Failed to write catalog index '{index_path}': {exc}"
        ) from exc


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
    namespace = _catalog_namespace(rel_path, CatalogItemTypeDirectory[item_type.name])
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


def _scan_tier_subdirectories(tier_dir: Path) -> tuple[list[CatalogIndexEntry], list[str]]:
    """Scan one tier's blueprints/steps subdirectories, returning the indexed entries and the skipped files' messages."""
    entries: list[CatalogIndexEntry] = []
    skipped: list[str] = []

    subdirs: list[tuple[CatalogItemType, Path]] = [
        (CatalogItemType.BLUEPRINT, tier_dir / "blueprints"),
        (CatalogItemType.STEP, tier_dir / "steps"),
    ]
    for item_type, sub_dir in subdirs:
        if not sub_dir.exists():
            continue
        for file_entry in Filesystem.scan_yaml_directory(sub_dir):
            entry, error = _index_entry_for_file(item_type, tier_dir, file_entry)
            if error is not None:
                skipped.append(error)
            elif entry is not None:
                entries.append(entry)

    return entries, skipped


def _global_tier_lock_path(paths: WorkspacePaths) -> Path:
    """Return the lock path used for GLOBAL/USER tier catalog mutations."""
    return resolve_lock_file_path(paths.global_paths.data_dir)


def scan_and_index_tier(tier: CatalogTier, paths: WorkspacePaths) -> CatalogScan:
    """Walk one tier's blueprints/steps directories, compute SHAs, and rewrite that tier's index.json wholesale.

    Raises:
        CatalogError: CATALOG_WRITE_FAILED if the tier's index.json cannot be written.
    """
    tier_dir = tier_root(tier, paths)
    tier_dir.mkdir(parents=True, exist_ok=True)

    lock_path = paths.lock_file if tier is CatalogTier.REPO else _global_tier_lock_path(paths)
    with WorkspaceLock(lock_path):
        entries, skipped = _scan_tier_subdirectories(tier_dir)
        write_catalog_index(tier_dir, CatalogIndex(items=entries))

        records = tuple(CatalogRecord(**entry.model_dump(), tier=tier) for entry in entries)
        return CatalogScan(records=records, skipped_files=tuple(skipped))


def scan_and_index_catalog(paths: WorkspacePaths) -> CatalogScan:
    """Scan and reindex the GLOBAL, USER, and REPO catalog tiers in precedence order, aggregating skipped files.

    Raises:
        CatalogError: CATALOG_WRITE_FAILED if any tier's index.json cannot be written.
    """
    records: list[CatalogRecord] = []
    skipped: list[str] = []
    for tier in (CatalogTier.GLOBAL, CatalogTier.USER, CatalogTier.REPO):
        scan = scan_and_index_tier(tier, paths)
        records.extend(scan.records)
        skipped.extend(scan.skipped_files)

    return CatalogScan(records=tuple(records), skipped_files=tuple(skipped))


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
    """Return initial template text for a catalog item or fall back to a default skeleton.

    A failed packaged read is logged at debug level and falls back to the built-in skeleton.
    """
    template_path = catalog_templates_dir / f"{type_enum.value}s" / "default.yml"
    try:
        content = template_path.read_text(encoding="utf-8")
        placeholder = "my-step" if type_enum == CatalogItemType.STEP else "my-blueprint"
        return content.replace(placeholder, stem)
    except Exception as exc:
        record_exception(
            exc,
            level=DiagnosticLevel.DEBUG,
            operation="catalog.template_fallback",
            context={"item_type": type_enum.value, "name": stem},
        )
        if type_enum == CatalogItemType.BLUEPRINT:
            return f'version: "1.0"\nname: {stem}\ndescription: Custom blueprint\nsteps: []\n'
        return f"id: {stem}\nname: {stem}\ndescription: Custom step blueprint\nrun: echo 'Hello from {stem}'\n"


def coerce_catalog_item_type(item_type: CatalogItemType | str) -> CatalogItemType:
    """Parse a catalog item type.

    Raises:
        CatalogError: CATALOG_TYPE_INVALID naming the allowed choices.
    """
    try:
        return item_type if isinstance(item_type, CatalogItemType) else CatalogItemType(str(item_type).lower())
    except ValueError:
        allowed = ", ".join([t.value for t in CatalogItemType])
        raise CatalogError(
            ErrorCode.CATALOG_TYPE_INVALID, f"Invalid item_type '{item_type}'. Allowed choices: {allowed}"
        ) from None


def create_catalog_item(
    item_type: CatalogItemType | str,
    name: str,
    tier: CatalogTier,
    paths: WorkspacePaths,
) -> CatalogRecord:
    """Create a new catalog item under the selected tier's `<type>s/<name>.yml` and reindex that tier.

    Raises:
        CatalogError: CATALOG_TYPE_INVALID for an unknown item_type, CATALOG_ALREADY_EXISTS when the file exists,
            CATALOG_WRITE_FAILED when writing or reindexing it fails.
    """
    lock_path = paths.lock_file if tier is CatalogTier.REPO else _global_tier_lock_path(paths)
    with WorkspaceLock(lock_path):
        type_enum = coerce_catalog_item_type(item_type)
        tier_dir = ensure_tier_catalog_dirs(tier, paths)
        stem = name[:-4] if name.endswith(".yml") or name.endswith(".yaml") else name
        filename = f"{stem}.yml"
        target_path = tier_dir / f"{type_enum.value}s" / filename

        if target_path.exists():
            rel_path = target_path.relative_to(tier_dir)
            raise CatalogError(ErrorCode.CATALOG_ALREADY_EXISTS, f"Catalog blueprint collision at path '{rel_path}'")

        content = _get_initial_template_content(type_enum, stem, paths.catalog_templates_dir)
        try:
            Filesystem.atomic_write_text(target_path, content)
        except OSError as exc:
            raise CatalogError(
                ErrorCode.CATALOG_WRITE_FAILED, f"Failed to write catalog item '{target_path}': {exc}"
            ) from exc

        scan = scan_and_index_tier(tier, paths)
        rel_path = target_path.relative_to(tier_dir)
        record = next((r for r in scan.records if r.path == rel_path), None)
        if record is None:
            raise CatalogError(
                ErrorCode.CATALOG_WRITE_FAILED, f"Failed to reindex newly created catalog blueprint '{rel_path}'."
            )
        return record


def resolve_deletable_catalog_item(sha_or_name: str, paths: WorkspacePaths) -> CatalogRecord:
    """Re-sync every tier and return the REPO-tier record a delete of sha_or_name would remove.

    Raises:
        CatalogError: CATALOG_NOT_FOUND when nothing matches sha_or_name,
            CATALOG_TIER_DELETE_FORBIDDEN if the resolved match is not indexed at the REPO tier,
            CATALOG_PROTECTED if attempting to delete a template in the protected 'dovo/' namespace,
            CATALOG_WRITE_FAILED if an index cannot be written while re-syncing.
        LockTimeoutError: If another process holds a tier lock during the re-sync.
    """
    if sha_or_name.startswith("dovo/"):
        raise CatalogError(ErrorCode.CATALOG_PROTECTED, f"Cannot delete bundled catalog template '{sha_or_name}'.")

    scan_and_index_catalog(paths)
    matches = find_catalog_record_matches(sha_or_name, None, paths)
    if not matches:
        raise CatalogError(ErrorCode.CATALOG_NOT_FOUND, f"Catalog blueprint '{sha_or_name}' not found.")

    item = matches[0]
    if item.tier != CatalogTier.REPO:
        raise CatalogError(
            ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN,
            f"Catalog item '{item.key}' resolved from tier '{item.tier.value}'; only repo-tier items can be deleted.",
        )

    if item.namespace == "dovo":
        raise CatalogError(ErrorCode.CATALOG_PROTECTED, f"Cannot delete bundled catalog template '{item.key}'.")
    return item


def delete_catalog_item(sha_or_name: str, item: CatalogRecord, paths: WorkspacePaths) -> None:
    """Remove a resolved REPO-tier item's file and reindex the REPO tier under the workspace lock.

    Raises:
        CatalogError: CATALOG_NOT_FOUND if the file was removed after sha_or_name was resolved,
            CATALOG_WRITE_FAILED if removing the file or reindexing fails.
        LockTimeoutError: If another process holds the workspace lock.
    """
    file_path = paths.catalog_dir / item.path
    with WorkspaceLock(paths.lock_file):
        if not file_path.exists():
            raise CatalogError(ErrorCode.CATALOG_NOT_FOUND, f"Catalog blueprint '{sha_or_name}' not found.")
        try:
            delete_file(file_path)
        except OSError as exc:
            raise CatalogError(
                ErrorCode.CATALOG_WRITE_FAILED, f"Failed to delete catalog item '{file_path}': {exc}"
            ) from exc

        scan_and_index_tier(CatalogTier.REPO, paths)
