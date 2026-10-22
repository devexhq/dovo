"""Integration tests for catalog scaffolding, protection, discovery fallback, and collisions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.models import DefinitionResolutionStatus
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.catalog.models import CatalogItemType, CatalogTier
from dovo.core.catalog.services.inventory import compute_catalog_sha
from tests.harness.diagnostic_log import FROZEN_EVENT_ID


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
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []


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
        assert result.fixes == []


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
        assert result.fixes == []


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
        assert result_second.fixes == []


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


class CatalogDiagnosticLogTests:
    """Tests verifying unexpected catalog failures are recorded with a hint and expected ones are not."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    @pytest.mark.parametrize("operation", ["catalog.create", "catalog.delete"])
    def test_unexpected_mutation_failure_records_error_and_appends_hint(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, operation: str
    ) -> None:
        """Return errors == [f"{permission message}\nDetails logged: {path} (event exc_3f9a1c07b2de)"] for a read-only blueprints dir and write an error record with that operation and context."""
        catalog = Catalog(workspace_paths)
        blueprints_dir = workspace_paths.catalog_blueprints_dir
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        if operation == "catalog.delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")
            denied_path, context = blueprints_dir / "demo.yml", {"sha_or_name": "demo"}
        else:
            denied_path, context = blueprints_dir / "demo.yml.tmp", {"item_type": "blueprint", "name": "demo"}
        blueprints_dir.chmod(0o555)
        try:
            if operation == "catalog.delete":
                errors = catalog.delete("demo").errors
            else:
                errors = catalog.create(CatalogItemType.BLUEPRINT, "demo").errors
        finally:
            blueprints_dir.chmod(0o755)

        permission_message = f"[Errno 13] Permission denied: '{denied_path}'"
        assert errors == [f"{permission_message}\nDetails logged: {frozen_diagnostic_clock} (event {FROZEN_EVENT_ID})"]
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "error"
        assert event["operation"] == operation
        assert event["context"] == context
        assert event["exception_type"] == "PermissionError"
        assert event["exception_message"] == permission_message

    @pytest.mark.parametrize("case", ["collision", "invalid_type", "protected_delete", "tier_delete", "not_found"])
    def test_expected_mutation_errors_are_not_recorded(self, workspace_paths: WorkspacePaths, case: str) -> None:
        """Return today's literal error message with no hint and leave <DOVO_HOME>/logs absent."""
        catalog = Catalog(workspace_paths)
        if case == "collision":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")
            errors = catalog.create(CatalogItemType.BLUEPRINT, "demo").errors
            expected = "Catalog blueprint collision at path 'blueprints/demo.yml'"
        elif case == "invalid_type":
            errors = catalog.create("widget", "demo").errors
            expected = "Invalid item_type 'widget'. Allowed choices: blueprint, step"
        elif case == "protected_delete":
            errors = catalog.delete("dovo/fix-tests").errors
            expected = "Cannot delete bundled catalog template 'dovo/fix-tests'."
        elif case == "tier_delete":
            _write_user_blueprint("user-only")
            errors = catalog.delete("user-only").errors
            expected = "Catalog item 'user-only' resolved from tier 'user'; only repo-tier items can be deleted."
        else:
            errors = catalog.delete("missing").errors
            expected = "Catalog blueprint 'missing' not found."

        assert errors == [expected]
        assert not resolve_global_paths(None).logs_dir.exists()

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
