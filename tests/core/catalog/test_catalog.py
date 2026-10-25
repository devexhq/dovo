"""Integration tests for catalog scaffolding, protection, discovery fallback, and collisions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.common.models import DefinitionResolutionStatus
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.catalog.models import (
    CatalogCreateResult,
    CatalogCreateStatus,
    CatalogDeleteResult,
    CatalogDeleteStatus,
    CatalogItemType,
    CatalogScanResult,
    CatalogTier,
)
from dovo.core.catalog.services.inventory import compute_catalog_sha
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.lock import raise_lock_timeout


class CatalogTemplateScaffoldingTests:
    """Tests verifying scaffolding of blueprints and steps under correct type subdirectories."""

    @pytest.mark.parametrize(
        ("item_type", "name", "expected_rel_path"),
        [
            pytest.param(
                CatalogItemType.BLUEPRINT, "scaffold-task", Path("blueprints/scaffold-task.yml"), id="blueprint"
            ),
            pytest.param(CatalogItemType.STEP, "scaffold-step", Path("steps/scaffold-step.yml"), id="step"),
        ],
    )
    def test_catalog_create_scaffolds_yaml_template_under_correct_type_dir(
        self,
        workspace_paths: WorkspacePaths,
        item_type: CatalogItemType,
        name: str,
        expected_rel_path: Path,
    ) -> None:
        catalog = Catalog(workspace_paths)
        result = catalog.create(item_type, name)

        target_file = workspace_paths.catalog_dir / expected_rel_path
        assert target_file.is_file()

        content = target_file.read_text(encoding="utf-8")
        expected_sha, expected_checksum = compute_catalog_sha(item_type, content)

        assert result.item is not None
        assert result.item.key == name
        assert result.item.sha == expected_sha
        assert result.item.item_type == item_type
        assert result.item.name == name
        assert result.item.namespace is None
        assert result.item.path == expected_rel_path
        assert result.item.checksum == expected_checksum
        assert result.item.tier == CatalogTier.REPO
        assert result.status == CatalogCreateStatus.OK
        assert result.error_code is None
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []


class CatalogProtectionTests:
    """Tests verifying protection of bundled templates in the dovo/ namespace."""

    @pytest.mark.parametrize(
        "template_name",
        [
            pytest.param("dovo/starter-task", id="starter-task"),
            pytest.param("dovo/fix-tests", id="fix-tests"),
        ],
    )
    def test_catalog_delete_rejects_deletion_of_bundled_packaged_templates(
        self, workspace_paths: WorkspacePaths, template_name: str
    ) -> None:
        catalog = Catalog(workspace_paths)
        result = catalog.delete(template_name)

        assert result.item is None
        assert result.deleted is False
        assert result.cancelled is False
        assert result.errors == [f"Cannot delete bundled catalog template '{template_name}'."]
        assert result.warnings == []
        assert result.remediations == []


class CatalogDiscoveryFallbackTests:
    """Tests verifying fallback to bundled templates in package resources."""

    @pytest.mark.parametrize(
        "template_name",
        [
            pytest.param("fix-tests", id="bare-name"),
            pytest.param("dovo/fix-tests", id="namespaced-name"),
        ],
    )
    def test_catalog_show_falls_back_to_bundled_template(
        self, workspace_paths: WorkspacePaths, template_name: str
    ) -> None:
        catalog = Catalog(workspace_paths)
        result = catalog.show(template_name)

        expected_file = Filesystem().catalog_templates_dir / "blueprints" / "dovo" / "fix-tests.yml"
        expected_content = expected_file.read_text(encoding="utf-8")

        assert result.item is not None
        assert result.item.tier == CatalogTier.PACKAGED
        assert result.content == expected_content
        assert result.template_matches == [("blueprints/dovo/fix-tests.yml", expected_content)]
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []


class CatalogCollisionTests:
    """Tests verifying collision detection during template creation."""

    @pytest.mark.parametrize(
        ("item_type", "name", "expected_rel_path"),
        [
            pytest.param(
                CatalogItemType.BLUEPRINT,
                "collision-task",
                Path("blueprints/collision-task.yml"),
                id="blueprint",
            ),
            pytest.param(
                CatalogItemType.STEP,
                "collision-step",
                Path("steps/collision-step.yml"),
                id="step",
            ),
        ],
    )
    def test_catalog_create_collision_returns_error(
        self,
        workspace_paths: WorkspacePaths,
        item_type: CatalogItemType,
        name: str,
        expected_rel_path: Path,
    ) -> None:
        catalog = Catalog(workspace_paths)
        result_first = catalog.create(item_type, name)
        assert result_first.item is not None

        target_file = workspace_paths.catalog_dir / expected_rel_path
        assert target_file.is_file()

        result_second = catalog.create(item_type, name)
        assert result_second.item is None
        assert result_second.errors == [f"Catalog blueprint collision at path '{expected_rel_path.as_posix()}'"]
        assert result_second.warnings == []
        assert result_second.remediations == []


class CatalogNamespaceSplittingTests:
    """Tests verifying namespaced identifiers split on the literal '/' boundary, not by character set."""

    def test_name_sharing_trailing_characters_with_namespace_resolves(self, workspace_paths: WorkspacePaths) -> None:
        catalog = Catalog(workspace_paths)
        catalog.save(
            "dovo/run-test",
            {"name": "run-test", "description": "Regression fixture", "action": "run"},
            item_type=CatalogItemType.STEP,
        )

        result = catalog.get("dovo/run-test", item_type=CatalogItemType.STEP)

        assert result.status == DefinitionResolutionStatus.OK
        assert result.resolved is not None
        assert result.resolved.name == "run-test"
        assert result.resolved.namespace == "dovo"


class CatalogGetTests:
    """Tests verifying multi-tier resolution precedence and duplicate-tier warnings for Catalog.get."""

    def test_get_returns_repo_tier_match_over_user_tier_duplicate(self, workspace_paths: WorkspacePaths) -> None:
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "shared-name")

        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "blueprints").mkdir(parents=True, exist_ok=True)
        (user_catalog_dir / "blueprints" / "shared-name.yml").write_text(
            'version: "1.0"\nname: shared-name\ndescription: User tier duplicate\nsteps: []\n', encoding="utf-8"
        )

        result = catalog.get("shared-name", item_type=CatalogItemType.BLUEPRINT)

        assert result.status == DefinitionResolutionStatus.OK
        assert result.resolved is not None
        assert result.resolved.tier == CatalogTier.REPO
        assert result.warnings == []

    def test_get_returns_user_tier_match_when_no_repo_tier_entry_exists(self, workspace_paths: WorkspacePaths) -> None:
        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "steps").mkdir(parents=True, exist_ok=True)
        (user_catalog_dir / "steps" / "user-only.yml").write_text(
            "name: user-only\ndescription: User tier step\naction: run\n", encoding="utf-8"
        )

        catalog = Catalog(workspace_paths)
        result = catalog.get("user-only", item_type=CatalogItemType.STEP)

        assert result.status == DefinitionResolutionStatus.OK
        assert result.resolved is not None
        assert result.resolved.tier == CatalogTier.USER


class CatalogDeleteTierGuardTests:
    """Tests verifying delete refuses to remove a match resolved from a non-REPO tier."""

    def test_delete_user_tier_match_returns_not_deletable_error(self, workspace_paths: WorkspacePaths) -> None:
        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "blueprints").mkdir(parents=True, exist_ok=True)
        user_file = user_catalog_dir / "blueprints" / "user-only.yml"
        user_file.write_text(
            'version: "1.0"\nname: user-only\ndescription: User tier blueprint\nsteps: []\n', encoding="utf-8"
        )

        catalog = Catalog(workspace_paths)
        result = catalog.delete("user-only")

        assert result.deleted is False
        assert result.errors == [
            "Catalog item 'user-only' resolved from tier 'user'; only repo-tier items can be deleted."
        ]
        assert user_file.is_file()


class CatalogTemplateFallbackLogTests:
    """Tests verifying an unreadable packaged template falls back to the skeleton and leaves a debug record."""

    def test_template_fallback_writes_debug_record_without_errors(
        self, workspace_paths: WorkspacePaths, tmp_path: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Create the skeleton blueprint with no errors when catalog_templates_dir is empty, and write one debug record with operation catalog.template_fallback and context {item_type, name}."""
        empty_templates = tmp_path / "empty-templates"
        empty_templates.mkdir()
        paths = workspace_paths.model_copy(update={"catalog_templates_dir": empty_templates})

        result = Catalog(paths).create(CatalogItemType.BLUEPRINT, "demo")

        assert result.errors == []
        assert (paths.catalog_blueprints_dir / "demo.yml").read_text(encoding="utf-8") == (
            'version: "1.0"\nname: demo\ndescription: Custom blueprint\nsteps: []\n'
        )
        assert list(frozen_diagnostic_clock.parent.iterdir()) == [frozen_diagnostic_clock]
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "debug"
        assert event["operation"] == "catalog.template_fallback"
        assert event["context"] == {"item_type": "blueprint", "name": "demo"}
        assert event["exception_type"] == "FileNotFoundError"


class _ExplodingBlueprintDefinition(BlueprintDefinition):
    """BlueprintDefinition whose document loading fails with an unexpected RuntimeError."""

    @classmethod
    def from_document(cls, raw: dict[str, object], *, key: str) -> BlueprintDefinition:
        raise RuntimeError("boom")


def _write_user_blueprint(name: str) -> None:
    """Write a minimal blueprint named name into the user catalog tier."""
    user_blueprints = resolve_global_paths(None).user_catalog_dir / "blueprints"
    user_blueprints.mkdir(parents=True, exist_ok=True)
    (user_blueprints / f"{name}.yml").write_text(
        f'version: "1.0"\nname: {name}\ndescription: User tier blueprint\nsteps: []\n', encoding="utf-8"
    )


def _run_classified_case(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch, case: str
) -> CatalogCreateResult | CatalogDeleteResult:
    """Drive catalog into the failure named by case and return its result."""
    if case == "invalid_type":
        return catalog.create("widget", "demo")
    if case == "collision":
        catalog.create(CatalogItemType.BLUEPRINT, "demo")
        return catalog.create(CatalogItemType.BLUEPRINT, "demo")
    if case == "reindex_miss":
        monkeypatch.setattr(
            "dovo.core.catalog.services.inventory.scan_and_index_tier", lambda tier, paths: CatalogScanResult()
        )
        return catalog.create(CatalogItemType.BLUEPRINT, "demo")
    if case == "protected_delete":
        return catalog.delete("dovo/fix-tests")
    if case == "tier_delete":
        _write_user_blueprint("user-only")
        return catalog.delete("user-only")
    return catalog.delete("missing")


class CatalogMutationOutcomeTests:
    """Tests verifying create/delete classify business failures and let everything else propagate."""

    @pytest.mark.parametrize(
        ("case", "expected_status", "expected_code"),
        [
            pytest.param(
                "invalid_type", CatalogCreateStatus.INVALID_TYPE, ErrorCode.CATALOG_TYPE_INVALID, id="invalid_type"
            ),
            pytest.param(
                "collision", CatalogCreateStatus.ALREADY_EXISTS, ErrorCode.CATALOG_ALREADY_EXISTS, id="already_exists"
            ),
            pytest.param(
                "reindex_miss", CatalogCreateStatus.WRITE_FAILED, ErrorCode.CATALOG_WRITE_FAILED, id="write_failed"
            ),
            pytest.param("not_found", CatalogDeleteStatus.NOT_FOUND, ErrorCode.CATALOG_NOT_FOUND, id="not_found"),
            pytest.param(
                "protected_delete", CatalogDeleteStatus.PROTECTED, ErrorCode.CATALOG_PROTECTED, id="protected"
            ),
            pytest.param(
                "tier_delete", CatalogDeleteStatus.WRONG_TIER, ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN, id="wrong_tier"
            ),
        ],
    )
    def test_classified_failure_returns_status_and_error_code(
        self,
        workspace_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        case: str,
        expected_status: CatalogCreateStatus | CatalogDeleteStatus,
        expected_code: ErrorCode,
    ) -> None:
        """Return (status, error_code) == (expected_status, expected_code) with one entry in errors, and leave <DOVO_HOME>/logs absent."""
        result = _run_classified_case(Catalog(workspace_paths), monkeypatch, case)

        assert (result.status, result.error_code) == (expected_status, expected_code)
        assert len(result.errors) == 1
        assert result.ok is False
        assert not resolve_global_paths(None).logs_dir.exists()

    @pytest.mark.parametrize("operation", ["create", "delete"])
    def test_lock_timeout_propagates(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, operation: str
    ) -> None:
        """Raise LockTimeoutError("lock held") unchanged from Catalog.create/delete when WorkspaceLock.__enter__ raises it, and leave <DOVO_HOME>/logs absent."""
        catalog = Catalog(workspace_paths)
        if operation == "delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        with pytest.raises(LockTimeoutError, match=r"^lock held$"):
            if operation == "delete":
                catalog.delete("demo")
            else:
                catalog.create(CatalogItemType.BLUEPRINT, "demo")

        assert not resolve_global_paths(None).logs_dir.exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    @pytest.mark.parametrize("operation", ["create", "delete"])
    def test_unexpected_failure_propagates_without_record(
        self, workspace_paths: WorkspacePaths, operation: str
    ) -> None:
        """Raise PermissionError from Catalog.create/delete on a read-only blueprints dir and leave <DOVO_HOME>/logs absent."""
        catalog = Catalog(workspace_paths)
        blueprints_dir = workspace_paths.catalog_blueprints_dir
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        if operation == "delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")
        blueprints_dir.chmod(0o555)
        try:
            with pytest.raises(PermissionError):
                if operation == "delete":
                    catalog.delete("demo")
                else:
                    catalog.create(CatalogItemType.BLUEPRINT, "demo")
        finally:
            blueprints_dir.chmod(0o755)

        assert not resolve_global_paths(None).logs_dir.exists()


class CatalogDiagnosticLogTests:
    """Tests verifying unexpected definition load failures are recorded with a hint and expected ones are not."""

    def test_unexpected_definition_load_failure_records_error(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return LOAD_ERROR with "Model validation failed for 'demo': boom" plus the hint when a BlueprintDefinition subclass's from_document raises RuntimeError, and record operation catalog.load_definition with context {key: demo}."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")

        result = catalog.get("demo", item_type=CatalogItemType.BLUEPRINT, definition_cls=_ExplodingBlueprintDefinition)

        assert result.status == DefinitionResolutionStatus.LOAD_ERROR
        assert result.errors == [
            f"Model validation failed for 'demo': boom\nDetails logged: {frozen_diagnostic_clock} (event {FROZEN_EVENT_ID})"
        ]
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "error"
        assert event["operation"] == "catalog.load_definition"
        assert event["context"] == {"key": "demo"}
        assert event["exception_type"] == "RuntimeError"

    def test_expected_definition_errors_are_not_recorded(self, workspace_paths: WorkspacePaths) -> None:
        """Return today's message with no hint for a schema-invalid blueprint and leave <DOVO_HOME>/logs absent."""
        catalog = Catalog(workspace_paths)
        catalog.save(
            "bad-timeout",
            {"name": "bad-timeout", "timeout_seconds": "not-a-number", "steps": []},
            item_type=CatalogItemType.BLUEPRINT,
        )

        result = catalog.get("bad-timeout", item_type=CatalogItemType.BLUEPRINT, definition_cls=BlueprintDefinition)

        assert result.status == DefinitionResolutionStatus.LOAD_ERROR
        assert len(result.errors) == 1
        assert result.errors[0].startswith(
            "Model validation failed for 'bad-timeout': Blueprint definition validation failed:"
        )
        assert "Details logged" not in result.errors[0]
        assert not resolve_global_paths(None).logs_dir.exists()
