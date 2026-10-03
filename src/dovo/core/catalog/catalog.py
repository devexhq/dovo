"""Catalog domain entrypoint: disk-only, multi-tier blueprint and step resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml

from dovo.common.exceptions import DefinitionLoadError, DefinitionValidationError
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.common.models import DefinitionResolutionResult, DefinitionResolutionStatus
from dovo.core.catalog.exceptions import (
    CatalogFileNotFoundError,
    CatalogWriteError,
    CatalogYamlError,
)
from dovo.core.catalog.models import (
    CatalogCreateResult,
    CatalogDeleteResult,
    CatalogItemType,
    CatalogListResult,
    CatalogRecord,
    CatalogScanResult,
    CatalogShowResult,
    CatalogTier,
    CatalogValidateResult,
    CatalogValidateStatus,
    DefinitionValidationOutcome,
    YamlParseOutcome,
)
from dovo.core.catalog.services.inventory import (
    coerce_catalog_item_type,
    create_catalog_item,
    delete_catalog_item_by_sha_or_name,
    ensure_tier_catalog_dirs,
    find_catalog_record_matches,
    list_packaged_template_defaults,
    resolve_catalog_record_path,
    resolve_catalog_records,
    scan_and_index_catalog,
    scan_and_index_tier,
)
from dovo.core.catalog.services.seeder import SeedResult, seed_all_catalog_templates
from dovo.core.catalog.services.validate import validate_catalog_item


class _PydanticModel(Protocol):
    """Minimal protocol for catalog definition classes validated via Pydantic."""

    @classmethod
    def model_validate(cls, obj: Any) -> Any:
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
        """Return indexed catalog records across all tiers, optionally filtered by item type."""
        filter_value = type_filter if type_filter is not None else kind
        scan_result = self.sync()
        records = resolve_catalog_records(self.paths)

        if filter_value is not None and str(filter_value).lower() == "template":
            return self._list_packaged_templates_result(records, scan_result)

        parsed_type, error = self._parse_filter_type(filter_value)
        if error is not None:
            return CatalogListResult(errors=[error])

        if parsed_type is not None:
            records = [record for record in records if record.item_type == parsed_type]

        return CatalogListResult(
            items=records,
            type_filter=parsed_type,
            warnings=list(scan_result.errors),
        )

    def _list_packaged_templates_result(
        self, records: list[CatalogRecord], scan_result: CatalogScanResult
    ) -> CatalogListResult:
        """Build the CatalogListResult for the '--type template' sugar filter."""
        packaged_items = [record for record in records if record.tier == CatalogTier.PACKAGED]
        return CatalogListResult(
            items=packaged_items,
            templates=list_packaged_template_defaults(self.paths.catalog_templates_dir),
            type_filter="template",
            warnings=list(scan_result.errors),
        )

    def _parse_filter_type(
        self, filter_value: CatalogItemType | str | None
    ) -> tuple[CatalogItemType | None, str | None]:
        """Parse optional type filter, returning (item_type, error_message)."""
        if filter_value is None:
            return None, None
        try:
            return coerce_catalog_item_type(filter_value), None
        except ValueError:
            allowed = ", ".join(t.value for t in CatalogItemType)
            return None, f"Invalid --type argument '{filter_value}'. Allowed choices: {allowed}"

    def show(self, sha_or_name: str, item_type: CatalogItemType | str | None = None) -> CatalogShowResult:
        """Show details and definition content of a catalog blueprint or packaged template."""
        resolution_result = self.get(sha_or_name, item_type=item_type)
        item = resolution_result.resolved
        if not resolution_result.ok or item is None:
            return CatalogShowResult(errors=[f"Catalog blueprint or template '{sha_or_name}' not found."])

        file_path = resolve_catalog_record_path(item, self.paths)
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            return CatalogShowResult(
                item=item,
                errors=[f"Failed to read file for catalog blueprint '{sha_or_name}': {exc}"],
            )

        template_matches = [(item.path.as_posix(), content)] if item.tier == CatalogTier.PACKAGED else []
        return CatalogShowResult(item=item, content=content, template_matches=template_matches)

    def get[T](
        self,
        key_or_sha: str,
        item_type: CatalogItemType | str | None = None,
        definition_cls: type[_PydanticModel] | None = None,
    ) -> DefinitionResolutionResult[CatalogRecord]:
        """Retrieve the highest-precedence catalog record matching key_or_sha, optionally validating its content into definition_cls."""
        self.sync()
        parsed_item_type = coerce_catalog_item_type(item_type) if item_type is not None else None
        matches = find_catalog_record_matches(key_or_sha, parsed_item_type, self.paths)

        if not matches:
            return DefinitionResolutionResult[CatalogRecord](
                status=DefinitionResolutionStatus.NOT_FOUND,
                requested_name=key_or_sha,
                resolved=None,
                matches=[],
                errors=[f"Catalog item '{key_or_sha}' not found."],
            )

        winner = matches[0]
        warnings: list[str] = []
        same_tier_matches = [match for match in matches if match.tier == winner.tier]
        if len(same_tier_matches) > 1:
            other_matching_paths = ", ".join(
                match.path.as_posix() for match in same_tier_matches if match.path != winner.path
            )
            warnings.append(
                f"Duplicate catalog name '{key_or_sha}' at tier '{winner.tier.value}'; using "
                f"'{winner.path.as_posix()}' (also found in: {other_matching_paths})."
            )

        definition: Any | None = None
        errors: list[str] = []
        status = DefinitionResolutionStatus.OK

        if definition_cls is not None:
            validation_outcome = self._validate_definition(winner, definition_cls, key_or_sha)
            definition = validation_outcome.definition
            status = validation_outcome.status
            errors = validation_outcome.errors

        return DefinitionResolutionResult[CatalogRecord](
            status=status,
            requested_name=key_or_sha,
            resolved=winner,
            definition=definition,
            matches=matches,
            errors=errors,
            warnings=warnings,
        )

    def create(
        self,
        item_type: CatalogItemType | str,
        name: str,
        *,
        tier: CatalogTier = CatalogTier.REPO,
    ) -> CatalogCreateResult:
        """Create a new catalog blueprint or step file at the selected tier and reindex."""
        try:
            record = create_catalog_item(coerce_catalog_item_type(item_type), name, tier, self.paths)
            resolved_path = resolve_catalog_record_path(record, self.paths)
            return CatalogCreateResult(item=record, resolved_path=resolved_path)
        except Exception as exc:
            return CatalogCreateResult(errors=[str(exc)])

    def delete(self, sha_or_name: str) -> CatalogDeleteResult:
        """Delete a REPO-tier catalog blueprint file and reindex, refusing dovo/-namespaced or non-REPO-tier matches."""
        try:
            deleted_item = delete_catalog_item_by_sha_or_name(sha_or_name, self.paths)
            if deleted_item is None:
                return CatalogDeleteResult(errors=[f"Catalog blueprint '{sha_or_name}' not found."])
            return CatalogDeleteResult(item=deleted_item, deleted=True)
        except Exception as exc:
            return CatalogDeleteResult(errors=[str(exc)])

    def save(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        item_type: CatalogItemType | str,
    ) -> CatalogRecord:
        """Write YAML under the REPO tier's type folder and reindex. Overwrites an existing file."""
        with WorkspaceLock(self.paths.lock_file):
            type_enum = coerce_catalog_item_type(item_type)
            catalog_dir = ensure_tier_catalog_dirs(CatalogTier.REPO, self.paths)
            stem = self._strip_yaml_suffix(name)
            rel_path = Path(f"{type_enum.value}s") / f"{stem}.yml"
            target_path = catalog_dir / rel_path
            text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False)

            if not text.endswith("\n"):
                text += "\n"
            try:
                Filesystem.atomic_write_text(target_path, text)
            except OSError as exc:
                raise CatalogWriteError(f"Failed to write catalog blueprint '{target_path}': {exc}") from exc

            scan_result = scan_and_index_tier(CatalogTier.REPO, self.paths)
            record = next((item for item in scan_result.items if item.path == rel_path), None)
            if record is None:
                raise CatalogWriteError(f"Failed to reindex catalog blueprint '{rel_path.as_posix()}'.")

            return record

    def seed(self, *, force: bool = False) -> SeedResult:
        """Seed packaged catalog templates into the workspace."""
        return seed_all_catalog_templates(self.paths, force=force)

    def sync(self) -> CatalogScanResult:
        """Synchronize each disk-backed tier's index.json with its on-disk YAML blueprints.

        Called automatically by every name/SHA-resolving method (``list``, ``get``, ``save``, and
        ``validate`` for a catalog-name target) so a lookup always sees the current contents of
        the GLOBAL, USER, and REPO catalog directories, never a stale index. ``validate`` is the one
        exception within itself: a direct file-path target is inspected without calling ``get``/``sync``
        at all, so validating a file never touches the index or acquires the workspace lock.
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
        resolution = self._resolve_validate_target(target, item_type=item_type)
        if isinstance(resolution, CatalogValidateResult):
            return resolution
        return validate_catalog_item(
            target,
            repo_root=self.path,
            resolved=resolution,
            blueprint_cls=blueprint_cls,
            step_cls=step_cls,
        )

    def _resolve_validate_target(
        self,
        target: str,
        *,
        item_type: CatalogItemType | None,
    ) -> CatalogValidateResult | tuple[Path, str, str, list[str]]:
        """Resolve target to (absolute_path, item_type_value, definition_key, warnings), or a terminal not-found/type-required result.

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
                    fixes=["Pass --type blueprint or --type step for a file target."],
                )
            return file_candidate, item_type.value, file_candidate.stem, []

        resolution = self.get(target)
        if resolution.resolved is None:
            return CatalogValidateResult(
                status=CatalogValidateStatus.NOT_FOUND,
                valid=False,
                target=target,
                resolved_path=None,
                item_type=None,
                errors=[f"Catalog item '{target}' not found (CATALOG_ITEM_NOT_FOUND)."],
                warnings=[],
                fixes=[f"Check that '{target}' names an indexed catalog item, or pass a file path instead."],
            )

        record = resolution.resolved
        resolved_path = resolve_catalog_record_path(record, self.paths)
        return resolved_path, record.item_type.value, record.key, list(resolution.warnings)

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

    @staticmethod
    def _strip_yaml_suffix(name: str) -> str:
        """Remove a trailing ``.yml`` / ``.yaml`` suffix when present."""
        if name.endswith(".yaml"):
            return name[:-5]
        if name.endswith(".yml"):
            return name[:-4]
        return name

    def _read_and_parse_yaml(self, file_path: Path, rel_path: Path) -> YamlParseOutcome:
        """Read and parse a catalog YAML file, returning parsed dict or error messages."""
        yaml_file = Filesystem.read_yaml_file(file_path)
        if yaml_file.error or yaml_file.parsed is None or not isinstance(yaml_file.parsed, dict):
            error_message = (
                yaml_file.error or f"Failed to load catalog blueprint '{rel_path}': invalid or non-object YAML content."
            )
            return YamlParseOutcome(parsed_data=None, errors=[error_message])
        return YamlParseOutcome(parsed_data=yaml_file.parsed, errors=[])

    def _validate_definition(
        self,
        winner: CatalogRecord,
        definition_cls: type[_PydanticModel],
        sha_or_name: str,
    ) -> DefinitionValidationOutcome:
        """Validate definition payload against model class, returning resolution outcome."""
        file_path = resolve_catalog_record_path(winner, self.paths)
        parse_outcome = self._read_and_parse_yaml(file_path, winner.path)
        if parse_outcome.errors or parse_outcome.parsed_data is None:
            return DefinitionValidationOutcome(
                definition=None,
                status=DefinitionResolutionStatus.LOAD_ERROR,
                errors=parse_outcome.errors,
            )

        parsed_data = parse_outcome.parsed_data
        schema_validator = getattr(definition_cls, "schema_validator", None)
        if schema_validator is not None and hasattr(schema_validator, "validate"):
            validation_result = schema_validator.validate(parsed_data)
            if hasattr(validation_result, "ok") and not validation_result.ok:
                validation_errors = list(getattr(validation_result, "errors", [str(validation_result)]))
                return DefinitionValidationOutcome(
                    definition=None,
                    status=DefinitionResolutionStatus.LOAD_ERROR,
                    errors=validation_errors,
                )

        try:
            from_document = getattr(definition_cls, "from_document", None)
            if callable(from_document):
                definition = from_document(parsed_data, key=winner.key or sha_or_name)
            else:
                definition = definition_cls.model_validate(parsed_data)
            return DefinitionValidationOutcome(
                definition=definition,
                status=DefinitionResolutionStatus.OK,
                errors=[],
            )
        except (Exception, DefinitionLoadError, DefinitionValidationError) as exc:
            return DefinitionValidationOutcome(
                definition=None,
                status=DefinitionResolutionStatus.LOAD_ERROR,
                errors=[f"Model validation failed for '{sha_or_name}': {exc}"],
            )
