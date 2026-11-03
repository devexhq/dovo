"""Catalog domain entrypoint: disk-only, multi-tier blueprint and step resolution."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, Self

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import Failure, catch_failure
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.catalog.exceptions import CatalogError, CatalogValidateFindings
from dovo.core.catalog.models import (
    CatalogCreateResult,
    CatalogDeleteResult,
    CatalogItemType,
    CatalogListResult,
    CatalogRecord,
    CatalogResolution,
    CatalogScan,
    CatalogShowResult,
    CatalogTier,
    CatalogValidateResult,
)
from dovo.core.catalog.services.inventory import (
    coerce_catalog_item_type,
    create_catalog_item,
    delete_catalog_item,
    find_catalog_record_matches,
    resolve_catalog_record_path,
    resolve_catalog_records,
    resolve_create_tier,
    resolve_deletable_catalog_item,
    scan_and_index_catalog,
)
from dovo.core.catalog.services.validate import check_catalog_item


class _PydanticModel(Protocol):
    """Minimal protocol for catalog definition classes validated via Pydantic."""

    @classmethod
    def model_validate(cls, obj: object) -> Self:
        """Validate and parse raw data into a model instance."""
        ...


class Catalog:
    """Unified entrypoint for blueprint catalog inventory and management."""

    def __init__(self, paths: WorkspacePaths) -> None:
        """Bind this Catalog to a resolved WorkspacePaths snapshot."""
        self.paths = paths
        self.path = paths.root_dir

    def list(self, item_type: CatalogItemType) -> CatalogListResult:
        """Return indexed catalog records of item_type across all tiers.

        Raises:
            LockTimeoutError: If another process holds a tier's workspace lock while reindexing.
        """
        with catch_failure("catalog.list", item_type=item_type.value) as caught:
            listed = self._list(item_type)
        if caught.failure is not None:
            return CatalogListResult(item_type=item_type, **caught.failure.result_fields())
        return listed

    def _list(self, item_type: CatalogItemType) -> CatalogListResult:
        """Reindex and list records of item_type, raising CatalogError on failure."""
        scan = self.sync()
        records = [record for record in resolve_catalog_records(self.paths) if record.item_type == item_type]
        return CatalogListResult(
            item_type=item_type,
            items=records,
            type_filter=item_type,
            warnings=builtins.list(scan.skipped_files),
        )

    def show(self, sha_or_name: str, item_type: CatalogItemType) -> CatalogShowResult:
        """Show details and definition content of the catalog item of item_type named sha_or_name."""
        with catch_failure("catalog.show", item_type=item_type.value, name=sha_or_name) as caught:
            shown = self._show(sha_or_name, item_type)
        if caught.failure is not None:
            return CatalogShowResult(item_type=item_type, **caught.failure.result_fields())
        return shown

    def _show(self, sha_or_name: str, item_type: CatalogItemType) -> CatalogShowResult:
        """Resolve sha_or_name and read its file into a CatalogShowResult, raising CatalogError on failure."""
        item = self.get(sha_or_name, item_type=item_type).record
        file_path = resolve_catalog_record_path(item, self.paths)
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(
                ErrorCode.CATALOG_FILE_UNREADABLE, f"Failed to read file for catalog blueprint '{sha_or_name}': {exc}"
            ) from None

        template_matches = [(item.path.as_posix(), content)] if item.tier == CatalogTier.PACKAGED else []
        return CatalogShowResult(item_type=item_type, item=item, content=content, template_matches=template_matches)

    def get(
        self,
        key_or_sha: str,
        item_type: CatalogItemType | str | None = None,
        definition_cls: type[_PydanticModel] | None = None,
    ) -> CatalogResolution:
        """Return the highest-precedence record matching key_or_sha, checking its content into definition_cls when given.

        A requested definition goes through the checks ``dovo blueprint validate`` applies to the winning record,
        each ``script_path`` looked up under the repository root, and its warnings follow the duplicate-name warning
        in the resolution's warnings.

        Raises:
            CatalogError: CATALOG_NOT_FOUND when nothing matches ("Catalog item '<key>' not found."),
                CATALOG_FILE_UNREADABLE, CATALOG_SYNTAX_INVALID or CATALOG_SCHEMA_INVALID, carrying every
                error and warning in ``CatalogValidateFindings``, when the winner fails those checks,
                CATALOG_WRITE_FAILED when reindexing fails, CATALOG_TYPE_INVALID for an unknown item_type.
        """
        self.sync()
        parsed_item_type = coerce_catalog_item_type(item_type) if item_type is not None else None
        matches = tuple(find_catalog_record_matches(key_or_sha, parsed_item_type, self.paths))
        if not matches:
            raise CatalogError(ErrorCode.CATALOG_NOT_FOUND, f"Catalog item '{key_or_sha}' not found.")

        winner = matches[0]
        warnings = _duplicate_name_warnings(key_or_sha, winner, matches)
        if definition_cls is None:
            return CatalogResolution(record=winner, definition=None, matches=matches, warnings=warnings)

        checked = check_catalog_item(
            resolve_catalog_record_path(winner, self.paths),
            winner.item_type.value,
            winner.key or key_or_sha,
            repo_root=self.path,
            definition_cls=definition_cls,
            prior_warnings=warnings,
        )
        return CatalogResolution(
            record=winner, definition=checked.definition, matches=matches, warnings=checked.warnings
        )

    def create(
        self,
        item_type: CatalogItemType,
        name: str,
        *,
        user: bool = False,
        global_: bool = False,
    ) -> CatalogCreateResult:
        """Create a new catalog blueprint or step file and reindex: at the REPO tier, or the USER or GLOBAL tier when user or global_ is set.

        Setting both user and global_ fails with CATALOG_TIER_CONFLICT before anything is read or written.

        Raises:
            LockTimeoutError: If another process holds the tier's workspace lock.
        """
        with catch_failure(
            "catalog.create", item_type=item_type.value, name=name, user=user, global_=global_
        ) as caught:
            created = self._create(item_type, name, resolve_create_tier(user=user, global_=global_))
        if caught.failure is not None:
            return CatalogCreateResult(item_type=item_type, **caught.failure.result_fields())
        return created

    def _create(self, item_type: CatalogItemType, name: str, tier: CatalogTier) -> CatalogCreateResult:
        """Create the item and return the ok result, raising CatalogError on failure."""
        record = create_catalog_item(item_type, name, tier, self.paths)
        resolved_path = resolve_catalog_record_path(record, self.paths)
        return CatalogCreateResult(item_type=item_type, item=record, resolved_path=resolved_path)

    def delete(
        self,
        item_type: CatalogItemType,
        sha_or_name: str,
        *,
        confirm: Callable[[CatalogRecord], bool] | None = None,
    ) -> CatalogDeleteResult:
        """Resolve, confirm and delete a REPO-tier catalog file of item_type, then reindex; confirm=None skips confirmation.

        sha_or_name is matched against items of item_type only: a name or sha of the other type fails with
        CATALOG_NOT_FOUND ("Catalog <item_type> '<name>' not found."), and the tier and namespace checks judge
        only the matches of item_type. An unknown name, a dovo/-namespaced template and a non-REPO-tier match
        fail without being confirmed.
        No lock is held while the prompt waits; the delete's workspace lock is taken after it. A declined
        confirmation returns the resolved, kept item with cancelled True, and a file removed after confirmation
        fails with CATALOG_NOT_FOUND.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        with catch_failure("catalog.delete", item_type=item_type.value, name=sha_or_name) as caught:
            item = resolve_deletable_catalog_item(item_type, sha_or_name, self.paths)
            if confirm is not None and not confirm(item):
                return CatalogDeleteResult(item_type=item_type, item=item, cancelled=True)
            delete_catalog_item(sha_or_name, item, self.paths)
        if caught.failure is not None:
            return CatalogDeleteResult(item_type=item_type, **caught.failure.result_fields())
        return CatalogDeleteResult(item_type=item_type, item=item, deleted=True)

    def sync(self) -> CatalogScan:
        """Synchronize each disk-backed tier's index.json with its on-disk YAML blueprints.

        Called automatically by every name/SHA-resolving method (``list``, ``get``, and ``validate``
        for a catalog-name target) so a lookup always sees the current contents of the GLOBAL, USER,
        and REPO catalog directories, never a stale index. ``validate`` is the one exception within
        itself: a direct file-path target is inspected without calling ``get``/``sync`` at all, so
        validating a file never touches the index or acquires the workspace lock.

        Raises:
            CatalogError: CATALOG_WRITE_FAILED when an index cannot be written.
        """
        return scan_and_index_catalog(self.paths)

    def validate(
        self,
        target: str,
        *,
        item_type: CatalogItemType,
        blueprint_cls: type[_PydanticModel],
        step_cls: type[_PydanticModel],
    ) -> CatalogValidateResult:
        """Validate a catalog blueprint or step definition without executing it.

        A catalog-name target is resolved via ``get()`` among items of item_type only, which re-syncs
        the index first, like every other name-based lookup on this class (see ``sync()``). A file-path
        target is inspected directly as item_type and never touches the index. Resolution warnings come
        before the definition's own, on success and failure alike.
        """
        resolution: tuple[Path, str, str, tuple[str, ...]] | None = None
        with catch_failure("catalog.validate", target=target) as caught:
            resolution = self._resolve_validate_target(target, item_type=item_type)
            resolved_path, item_type_value, key, resolution_warnings = resolution
            checked = check_catalog_item(
                resolved_path,
                item_type_value,
                key,
                repo_root=self.path,
                definition_cls=step_cls if item_type_value == CatalogItemType.STEP.value else blueprint_cls,
                prior_warnings=resolution_warnings,
            )
        if caught.failure is not None:
            if resolution is None:
                return _target_lookup_failure(target, item_type, caught.failure)
            return _definition_failure(target, item_type, resolution, caught.failure)
        return CatalogValidateResult(
            valid=True,
            target=target,
            resolved_path=resolved_path,
            item_type=item_type,
            warnings=builtins.list(checked.warnings),
        )

    def _resolve_validate_target(
        self,
        target: str,
        *,
        item_type: CatalogItemType,
    ) -> tuple[Path, str, str, tuple[str, ...]]:
        """Resolve target to (absolute_path, item_type_value, definition_key, warnings), looking a catalog name up among items of item_type only.

        Raises:
            CatalogError: When a catalog-name target cannot be resolved (see ``get()``).

        Kept on ``Catalog`` rather than in ``services/validate.py`` so the catalog-name branch can call
        ``self.get()`` directly instead of duplicating its key/SHA lookup logic;
        ``services/validate.py`` never imports ``Catalog``, which would create an import cycle
        (``catalog.py`` already imports ``services/validate.py`` for the YAML/schema/semantic checks).
        """
        candidate = Path(target)
        file_candidate = candidate if candidate.is_absolute() else self.path / candidate

        if file_candidate.is_file():
            return file_candidate, item_type.value, file_candidate.stem, ()

        resolution = self.get(target, item_type=item_type)
        record = resolution.record
        resolved_path = resolve_catalog_record_path(record, self.paths)
        return resolved_path, record.item_type.value, record.key, resolution.warnings

    @staticmethod
    def read_yaml(path: Path) -> dict[str, Any]:
        """Load a YAML object from path or raise a classified catalog error."""
        if not path.exists():
            raise CatalogError(ErrorCode.CATALOG_NOT_FOUND, f"Catalog file not found at '{path}'.")
        yaml_file = Filesystem.read_yaml_file(path)
        if yaml_file.error or yaml_file.parsed is None or not isinstance(yaml_file.parsed, dict):
            detail = yaml_file.error or "invalid or non-object YAML content."
            raise CatalogError(ErrorCode.CATALOG_SYNTAX_INVALID, f"Failed to load catalog blueprint '{path}': {detail}")
        return yaml_file.parsed


def _duplicate_name_warnings(
    key_or_sha: str, winner: CatalogRecord, matches: tuple[CatalogRecord, ...]
) -> tuple[str, ...]:
    """Return the 'Duplicate catalog name …' warning when the winner's tier holds more than one match, else ()."""
    same_tier_matches = [match for match in matches if match.tier == winner.tier]
    if len(same_tier_matches) <= 1:
        return ()
    other_matching_paths = ", ".join(match.path.as_posix() for match in same_tier_matches if match.path != winner.path)
    return (
        f"Duplicate catalog name '{key_or_sha}' at tier '{winner.tier.value}'; using "
        f"'{winner.path.as_posix()}' (also found in: {other_matching_paths}).",
    )


def _target_lookup_failure(target: str, item_type: CatalogItemType, failure: Failure) -> CatalogValidateResult:
    """Build the failed validate result from a failed target lookup: its code, message, remediations and recorded diagnostic, or the item_type-scoped catalog-name remediation for CATALOG_NOT_FOUND."""
    fields = failure.result_fields()
    if failure.error_code is ErrorCode.CATALOG_NOT_FOUND:
        fields["remediations"] = [
            Remediation(
                message=f"Check that '{target}' names an indexed {item_type.value}, or pass a file path instead."
            )
        ]
    return CatalogValidateResult(
        valid=False, target=target, resolved_path=None, item_type=item_type, warnings=[], **fields
    )


def _definition_failure(
    target: str, item_type: CatalogItemType, resolution: tuple[Path, str, str, tuple[str, ...]], failure: Failure
) -> CatalogValidateResult:
    """Build the failed validate result for a resolved target: the failure's fields with every error and warning its findings carry, which start with the resolution warnings, or the resolution warnings alone for an unexpected failure."""
    resolved_path, _, _, warnings = resolution
    fields = failure.result_fields()
    findings = failure.data
    if isinstance(findings, CatalogValidateFindings):
        fields["errors"] = builtins.list(findings.errors)
        warnings = findings.warnings
    return CatalogValidateResult(
        valid=False,
        target=target,
        resolved_path=resolved_path,
        item_type=item_type,
        warnings=builtins.list(warnings),
        **fields,
    )
