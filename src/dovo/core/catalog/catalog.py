"""Catalog domain entrypoint: disk-only, multi-tier blueprint and step resolution."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any, Protocol, Self

from pydantic import ValidationError

from dovo.common.diagnostic_log import append_diagnostic_hint
from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DefinitionLoadError, DefinitionValidationError, Failure, catch_failure
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.catalog.exceptions import (
    CatalogError,
    CatalogFileNotFoundError,
    CatalogYamlError,
)
from dovo.core.catalog.models import (
    CatalogCreateResult,
    CatalogCreateStatus,
    CatalogDeleteResult,
    CatalogDeleteStatus,
    CatalogItemType,
    CatalogListResult,
    CatalogRecord,
    CatalogResolution,
    CatalogScan,
    CatalogShowResult,
    CatalogTier,
    CatalogValidateResult,
    CatalogValidateStatus,
    YamlParseOutcome,
)
from dovo.core.catalog.services.inventory import (
    coerce_catalog_item_type,
    create_catalog_item,
    delete_catalog_item_by_sha_or_name,
    find_catalog_record_matches,
    list_packaged_template_defaults,
    resolve_catalog_record_path,
    resolve_catalog_records,
    scan_and_index_catalog,
)
from dovo.core.catalog.services.validate import validate_catalog_item


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

    def list(
        self,
        kind: CatalogItemType | str | None = None,
        *,
        type_filter: CatalogItemType | str | None = None,
    ) -> CatalogListResult:
        """Return indexed catalog records across all tiers, optionally filtered by item type.

        Raises:
            LockTimeoutError: If another process holds a tier's workspace lock while reindexing.
        """
        filter_value = type_filter if type_filter is not None else kind
        with catch_failure("catalog.list", type_filter=None if filter_value is None else str(filter_value)) as caught:
            listed = self._list(filter_value)
        if caught.failure is not None:
            return CatalogListResult(
                error_code=caught.failure.error_code,
                errors=[_failure_message(caught.failure)],
                remediations=list(caught.failure.remediations),
            )
        return listed

    def _list(self, filter_value: CatalogItemType | str | None) -> CatalogListResult:
        """Reindex and list records matching filter_value, raising CatalogError on failure."""
        scan = self.sync()
        records = resolve_catalog_records(self.paths)

        if filter_value is not None and str(filter_value).lower() == "template":
            return self._list_packaged_templates_result(records, scan)

        parsed_type = self._parse_filter_type(filter_value)
        if parsed_type is not None:
            records = [record for record in records if record.item_type == parsed_type]

        return CatalogListResult(
            items=records,
            type_filter=parsed_type,
            warnings=list(scan.skipped_files),
        )

    def _list_packaged_templates_result(
        self, records: builtins.list[CatalogRecord], scan: CatalogScan
    ) -> CatalogListResult:
        """Build the CatalogListResult for the '--type template' sugar filter."""
        packaged_items = [record for record in records if record.tier == CatalogTier.PACKAGED]
        return CatalogListResult(
            items=packaged_items,
            templates=list_packaged_template_defaults(self.paths.catalog_templates_dir),
            type_filter="template",
            warnings=list(scan.skipped_files),
        )

    def _parse_filter_type(self, filter_value: CatalogItemType | str | None) -> CatalogItemType | None:
        """Parse the optional type filter.

        Raises:
            CatalogError: CATALOG_TYPE_INVALID for an unknown type.
        """
        return coerce_catalog_item_type(filter_value) if filter_value is not None else None

    def show(self, sha_or_name: str, item_type: CatalogItemType | str | None = None) -> CatalogShowResult:
        """Show details and definition content of a catalog blueprint or packaged template."""
        with catch_failure("catalog.show", name=sha_or_name) as caught:
            shown = self._show(sha_or_name, item_type)
        if caught.failure is not None:
            return CatalogShowResult(
                error_code=caught.failure.error_code,
                errors=[_failure_message(caught.failure)],
                remediations=list(caught.failure.remediations),
            )
        return shown

    def _show(self, sha_or_name: str, item_type: CatalogItemType | str | None) -> CatalogShowResult:
        """Resolve sha_or_name and read its file into a CatalogShowResult, raising CatalogError on failure."""
        item = self.get(sha_or_name, item_type=item_type).record
        file_path = resolve_catalog_record_path(item, self.paths)
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogYamlError(f"Failed to read file for catalog blueprint '{sha_or_name}': {exc}") from exc

        template_matches = [(item.path.as_posix(), content)] if item.tier == CatalogTier.PACKAGED else []
        return CatalogShowResult(item=item, content=content, template_matches=template_matches)

    def get(
        self,
        key_or_sha: str,
        item_type: CatalogItemType | str | None = None,
        definition_cls: type[_PydanticModel] | None = None,
    ) -> CatalogResolution:
        """Return the highest-precedence record matching key_or_sha, validating its content into definition_cls when given.

        Raises:
            CatalogError: CATALOG_NOT_FOUND when nothing matches ("Catalog item '<key>' not found."),
                CATALOG_SYNTAX_ERROR or CATALOG_SCHEMA_INVALID when the winner cannot be loaded into
                definition_cls, CATALOG_WRITE_FAILED when reindexing fails, CATALOG_TYPE_INVALID for an
                unknown item_type.
        """
        self.sync()
        parsed_item_type = coerce_catalog_item_type(item_type) if item_type is not None else None
        matches = find_catalog_record_matches(key_or_sha, parsed_item_type, self.paths)
        if not matches:
            raise CatalogError(ErrorCode.CATALOG_NOT_FOUND, f"Catalog item '{key_or_sha}' not found.")

        winner = matches[0]
        definition = self._load_definition(winner, definition_cls, key_or_sha) if definition_cls is not None else None
        return CatalogResolution(record=winner, definition=definition, matches=tuple(matches))

    def create(
        self,
        item_type: CatalogItemType | str,
        name: str,
        *,
        tier: CatalogTier = CatalogTier.REPO,
    ) -> CatalogCreateResult:
        """Create a new catalog blueprint or step file at the selected tier and reindex.

        Raises:
            LockTimeoutError: If another process holds the tier's workspace lock.
        """
        with catch_failure("catalog.create", item_type=str(item_type), name=name, tier=tier.value) as caught:
            created = self._create(item_type, name, tier)
        if caught.failure is not None:
            return CatalogCreateResult(
                status=_create_status(caught.failure.error_code),
                error_code=caught.failure.error_code,
                errors=[_failure_message(caught.failure)],
                remediations=list(caught.failure.remediations),
            )
        return created

    def _create(self, item_type: CatalogItemType | str, name: str, tier: CatalogTier) -> CatalogCreateResult:
        """Create the item and return the ok result, raising CatalogError on failure."""
        record = create_catalog_item(item_type, name, tier, self.paths)
        resolved_path = resolve_catalog_record_path(record, self.paths)
        return CatalogCreateResult(status=CatalogCreateStatus.OK, item=record, resolved_path=resolved_path)

    def delete(self, sha_or_name: str) -> CatalogDeleteResult:
        """Delete a REPO-tier catalog blueprint file and reindex, refusing dovo/-namespaced or non-REPO-tier matches.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        with catch_failure("catalog.delete", name=sha_or_name) as caught:
            deleted_item = delete_catalog_item_by_sha_or_name(sha_or_name, self.paths)
        if caught.failure is not None:
            return CatalogDeleteResult(
                status=_delete_status(caught.failure.error_code),
                error_code=caught.failure.error_code,
                errors=[_failure_message(caught.failure)],
                remediations=list(caught.failure.remediations),
            )
        return CatalogDeleteResult(status=CatalogDeleteStatus.OK, item=deleted_item, deleted=True)

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
        item_type: CatalogItemType | None = None,
        blueprint_cls: type[_PydanticModel],
        step_cls: type[_PydanticModel],
    ) -> CatalogValidateResult:
        """Validate a catalog blueprint or step definition without executing it.

        A catalog-name target is resolved via ``get()``, which re-syncs the index first, like every
        other name-based lookup on this class (see ``sync()``). A file-path target is inspected
        directly and never touches the index.
        """
        with catch_failure("catalog.validate", target=target) as caught:
            resolution = self._resolve_validate_target(target, item_type=item_type)
        if caught.failure is not None:
            return _target_not_found(target, caught.failure)
        if isinstance(resolution, CatalogValidateResult):
            return resolution
        with catch_failure("catalog.validate", target=target) as caught:
            validated = validate_catalog_item(
                target,
                repo_root=self.path,
                resolved=resolution,
                blueprint_cls=blueprint_cls,
                step_cls=step_cls,
            )
        if caught.failure is not None:
            resolved_path, item_type_value, _, warnings = resolution
            return CatalogValidateResult(
                status=CatalogValidateStatus.INVALID,
                valid=False,
                target=target,
                resolved_path=resolved_path,
                item_type=item_type_value,
                error_code=caught.failure.error_code,
                errors=[_failure_message(caught.failure)],
                warnings=warnings,
                remediations=list(caught.failure.remediations),
            )
        return validated

    def _resolve_validate_target(
        self,
        target: str,
        *,
        item_type: CatalogItemType | None,
    ) -> CatalogValidateResult | tuple[Path, str, str, builtins.list[str]]:
        """Resolve target to (absolute_path, item_type_value, definition_key, warnings), or a terminal type-required result.

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
            if item_type is None:
                return CatalogValidateResult(
                    status=CatalogValidateStatus.TYPE_REQUIRED,
                    valid=False,
                    target=target,
                    resolved_path=file_candidate,
                    item_type=None,
                    errors=[f"'--type' is required to validate file target '{target}' (CATALOG_TYPE_REQUIRED)."],
                    warnings=[],
                    remediations=[Remediation(message="Pass '--type blueprint' or '--type step' for a file target.")],
                )
            return file_candidate, item_type.value, file_candidate.stem, []

        resolution = self.get(target)
        record = resolution.record
        resolved_path = resolve_catalog_record_path(record, self.paths)
        return resolved_path, record.item_type.value, record.key, _duplicate_name_warnings(target, resolution)

    @staticmethod
    def read_yaml(path: Path) -> dict[str, Any]:
        """Load a YAML object from path or raise a classified catalog error."""
        if not path.exists():
            raise CatalogFileNotFoundError(f"Catalog file not found at '{path}'.")
        yaml_file = Filesystem.read_yaml_file(path)
        if yaml_file.error or yaml_file.parsed is None or not isinstance(yaml_file.parsed, dict):
            detail = yaml_file.error or "invalid or non-object YAML content."
            raise CatalogYamlError(f"Failed to load catalog blueprint '{path}': {detail}")
        return yaml_file.parsed

    def _read_and_parse_yaml(self, file_path: Path, rel_path: Path) -> YamlParseOutcome:
        """Read and parse a catalog YAML file, returning parsed dict or error messages."""
        yaml_file = Filesystem.read_yaml_file(file_path)
        if yaml_file.error or yaml_file.parsed is None or not isinstance(yaml_file.parsed, dict):
            error_message = (
                yaml_file.error or f"Failed to load catalog blueprint '{rel_path}': invalid or non-object YAML content."
            )
            return YamlParseOutcome(parsed_data=None, errors=[error_message])
        return YamlParseOutcome(parsed_data=yaml_file.parsed, errors=[])

    def _load_definition(
        self,
        record: CatalogRecord,
        definition_cls: type[_PydanticModel],
        key_or_sha: str,
    ) -> object:
        """Read record's YAML and build definition_cls from it.

        Raises:
            CatalogError: CATALOG_SYNTAX_ERROR when the YAML is unreadable or not an object, CATALOG_SCHEMA_INVALID
                ("Model validation failed for '<key_or_sha>': <exc>") when the definition fails validation.
        """
        parse_outcome = self._read_and_parse_yaml(resolve_catalog_record_path(record, self.paths), record.path)
        if parse_outcome.errors or parse_outcome.parsed_data is None:
            raise CatalogYamlError(parse_outcome.errors[0])

        from_document = getattr(definition_cls, "from_document", None)
        try:
            if callable(from_document):
                return from_document(parse_outcome.parsed_data, key=record.key or key_or_sha)
            return definition_cls.model_validate(parse_outcome.parsed_data)
        except (ValidationError, DefinitionLoadError, DefinitionValidationError) as exc:
            raise CatalogError(
                ErrorCode.CATALOG_SCHEMA_INVALID, f"Model validation failed for '{key_or_sha}': {exc}"
            ) from exc


def _duplicate_name_warnings(key_or_sha: str, resolution: CatalogResolution) -> builtins.list[str]:
    """Return the 'Duplicate catalog name …' warning when the winner's tier holds more than one match, else []."""
    winner = resolution.record
    same_tier_matches = [match for match in resolution.matches if match.tier == winner.tier]
    if len(same_tier_matches) <= 1:
        return []
    other_matching_paths = ", ".join(match.path.as_posix() for match in same_tier_matches if match.path != winner.path)
    return [
        f"Duplicate catalog name '{key_or_sha}' at tier '{winner.tier.value}'; using "
        f"'{winner.path.as_posix()}' (also found in: {other_matching_paths})."
    ]


def _create_status(error_code: str | None) -> CatalogCreateStatus:
    """Return the create status for a caught failure's code; internal_error for any code create does not raise."""
    match error_code:
        case ErrorCode.CATALOG_TYPE_INVALID:
            return CatalogCreateStatus.INVALID_TYPE
        case ErrorCode.CATALOG_ALREADY_EXISTS:
            return CatalogCreateStatus.ALREADY_EXISTS
        case ErrorCode.CATALOG_WRITE_FAILED:
            return CatalogCreateStatus.WRITE_FAILED
        case _:
            return CatalogCreateStatus.INTERNAL_ERROR


def _delete_status(error_code: str | None) -> CatalogDeleteStatus:
    """Return the delete status for a caught failure's code; internal_error for any code delete does not raise."""
    match error_code:
        case ErrorCode.CATALOG_NOT_FOUND:
            return CatalogDeleteStatus.NOT_FOUND
        case ErrorCode.CATALOG_PROTECTED:
            return CatalogDeleteStatus.PROTECTED
        case ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN:
            return CatalogDeleteStatus.WRONG_TIER
        case ErrorCode.CATALOG_WRITE_FAILED:
            return CatalogDeleteStatus.WRITE_FAILED
        case _:
            return CatalogDeleteStatus.INTERNAL_ERROR


def _failure_message(failure: Failure) -> str:
    """Return failure.message with its Details logged line when a diagnostic was recorded."""
    return append_diagnostic_hint(failure.message, failure.diagnostic)


def _target_not_found(target: str, failure: Failure) -> CatalogValidateResult:
    """Build the not_found validate result from a failed target lookup: its code, message and remediations, or the catalog-name remediation for CATALOG_NOT_FOUND."""
    remediations = (
        [Remediation(message=f"Check that '{target}' names an indexed catalog item, or pass a file path instead.")]
        if failure.error_code is ErrorCode.CATALOG_NOT_FOUND
        else list(failure.remediations)
    )
    return CatalogValidateResult(
        status=CatalogValidateStatus.NOT_FOUND,
        valid=False,
        target=target,
        resolved_path=None,
        item_type=None,
        error_code=failure.error_code,
        errors=[_failure_message(failure)],
        warnings=[],
        remediations=remediations,
    )
