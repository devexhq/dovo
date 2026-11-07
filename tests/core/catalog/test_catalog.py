"""Integration tests for catalog scaffolding, protection, discovery fallback, and collisions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.catalog import Catalog, CatalogError
from dovo.core.catalog.definitions import BlueprintDefinition, StepDefinition
from dovo.core.catalog.exceptions import CatalogValidateFindings
from dovo.core.catalog.models import (
    CatalogCreateResult,
    CatalogDeleteResult,
    CatalogItemType,
    CatalogListResult,
    CatalogRecord,
    CatalogResult,
    CatalogScan,
    CatalogShowResult,
    CatalogTier,
    CatalogValidateResult,
)
from dovo.core.catalog.services.inventory import compute_catalog_sha
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
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
        assert result.error_code is None
        assert result.ok
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
        result = catalog.delete(CatalogItemType.BLUEPRINT, template_name)

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
        result = catalog.show(template_name, CatalogItemType.BLUEPRINT)

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
        step_file = workspace_paths.catalog_dir / "steps" / "dovo" / "run-test.yml"
        step_file.parent.mkdir(parents=True, exist_ok=True)
        step_file.write_text(
            yaml.safe_dump({"name": "run-test", "description": "Regression fixture", "action": "run"}),
            encoding="utf-8",
        )

        resolution = catalog.get("dovo/run-test", item_type=CatalogItemType.STEP)

        assert resolution.record.name == "run-test"
        assert resolution.record.namespace == "dovo"


class CatalogGetTests:
    """Tests verifying multi-tier resolution precedence and classified failures for Catalog.get."""

    def test_get_returns_repo_tier_match_over_user_tier_duplicate(self, workspace_paths: WorkspacePaths) -> None:
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "shared-name")
        _write_user_blueprint("shared-name")

        resolution = catalog.get("shared-name", item_type=CatalogItemType.BLUEPRINT)

        assert resolution.record.tier == CatalogTier.REPO
        assert [match.tier for match in resolution.matches] == [CatalogTier.REPO, CatalogTier.USER]
        assert resolution.definition is None

    def test_get_returns_user_tier_match_when_no_repo_tier_entry_exists(self, workspace_paths: WorkspacePaths) -> None:
        user_catalog_dir = resolve_global_paths(None).user_catalog_dir
        (user_catalog_dir / "steps").mkdir(parents=True, exist_ok=True)
        (user_catalog_dir / "steps" / "user-only.yml").write_text(
            "name: user-only\ndescription: User tier step\naction: run\n", encoding="utf-8"
        )

        catalog = Catalog(workspace_paths)
        resolution = catalog.get("user-only", item_type=CatalogItemType.STEP)

        assert resolution.record.tier == CatalogTier.USER

    def test_get_validates_definition_into_resolution(self, workspace_paths: WorkspacePaths) -> None:
        """Return CatalogResolution with record.key == 'demo', a BlueprintDefinition named 'demo', and matches == (record,)."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")

        resolution = catalog.get("demo", item_type=CatalogItemType.BLUEPRINT, definition_cls=BlueprintDefinition)

        assert resolution.record.key == "demo"
        assert isinstance(resolution.definition, BlueprintDefinition)
        assert resolution.definition.name == "demo"
        assert resolution.matches == (resolution.record,)

    def test_get_missing_raises_catalog_not_found(self, workspace_paths: WorkspacePaths) -> None:
        """Raise CatalogError with error_code CATALOG_NOT_FOUND, message "Catalog item 'ghost' not found.", remediations ()."""
        with pytest.raises(CatalogError) as raised:
            Catalog(workspace_paths).get("ghost")

        assert (raised.value.error_code, raised.value.message, raised.value.remediations) == (
            ErrorCode.CATALOG_NOT_FOUND,
            "Catalog item 'ghost' not found.",
            (),
        )

    @pytest.mark.parametrize(
        ("content", "code"),
        [
            pytest.param("- a\n- b\n", ErrorCode.CATALOG_SYNTAX_INVALID, id="non_mapping"),
            pytest.param("name: bad\nsteps: [\n", ErrorCode.CATALOG_SYNTAX_INVALID, id="syntax"),
            pytest.param("name: bad\ntimeout_seconds: x\nsteps: []\n", ErrorCode.CATALOG_SCHEMA_INVALID, id="schema"),
            pytest.param(
                "name: bad\nsteps: [{id: a, run: one}, {id: a, run: two}]\n",
                ErrorCode.CATALOG_SCHEMA_INVALID,
                id="duplicate_step_id",
            ),
        ],
    )
    def test_get_unloadable_definition_raises_the_validate_failure(
        self, workspace_paths: WorkspacePaths, content: str, code: ErrorCode
    ) -> None:
        """Raise CatalogError with the code, errors and warnings `dovo blueprint validate` reports, and leave <DOVO_HOME>/logs absent."""
        blueprint_file = workspace_paths.catalog_blueprints_dir / "bad.yml"
        blueprint_file.parent.mkdir(parents=True, exist_ok=True)
        blueprint_file.write_text(content, encoding="utf-8")
        catalog = Catalog(workspace_paths)

        with pytest.raises(CatalogError) as raised:
            catalog.get("bad", item_type=CatalogItemType.BLUEPRINT, definition_cls=BlueprintDefinition)

        validated = catalog.validate(
            "bad", item_type=CatalogItemType.BLUEPRINT, blueprint_cls=BlueprintDefinition, step_cls=StepDefinition
        )
        findings = raised.value.data
        assert isinstance(findings, CatalogValidateFindings)
        assert (raised.value.error_code, list(findings.errors), list(findings.warnings)) == (
            code,
            validated.errors,
            validated.warnings,
        )
        assert list(raised.value.remediations) == validated.remediations
        assert "errors.pydantic.dev" not in " ".join(findings.errors)
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_get_with_definition_returns_the_validate_warnings(self, workspace_paths: WorkspacePaths) -> None:
        blueprint_file = workspace_paths.catalog_blueprints_dir / "scripted.yml"
        blueprint_file.parent.mkdir(parents=True, exist_ok=True)
        blueprint_file.write_text(
            "name: scripted\nsteps: [{id: build, type: script, script_path: scripts/build.sh}]\n", encoding="utf-8"
        )

        resolution = Catalog(workspace_paths).get(
            "scripted", item_type=CatalogItemType.BLUEPRINT, definition_cls=BlueprintDefinition
        )

        assert resolution.warnings == (
            "Step 'build' script_path 'scripts/build.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND).",
        )

    def test_get_unexpected_definition_error_propagates_unrecorded(self, workspace_paths: WorkspacePaths) -> None:
        """Raise RuntimeError("boom") from get(definition_cls=_ExplodingBlueprintDefinition) and leave <DOVO_HOME>/logs absent."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")

        with pytest.raises(RuntimeError, match=r"^boom$"):
            catalog.get("demo", item_type=CatalogItemType.BLUEPRINT, definition_cls=_ExplodingBlueprintDefinition)

        assert not resolve_global_paths(None).logs_dir.exists()


class CatalogShowTests:
    """Tests verifying Catalog.show copies a caught failure into its result."""

    def test_show_failure_copies_error(self, workspace_paths: WorkspacePaths) -> None:
        """Return CatalogShowResult with error_code CATALOG_NOT_FOUND, errors == ["Catalog item 'ghost' not found."], item None, ok False."""
        result = Catalog(workspace_paths).show("ghost", CatalogItemType.BLUEPRINT)

        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.errors == ["Catalog item 'ghost' not found."]
        assert result.item is None
        assert result.ok is False

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_show_unreadable_file_records_no_diagnostic(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Return error_code CATALOG_FILE_UNREADABLE and the read error alone, with empty diagnostics and no event file."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")
        # Keep the index written by create, so demo stays indexed after its file becomes unreadable.
        monkeypatch.setattr(
            "dovo.core.catalog.catalog.scan_and_index_catalog",
            lambda paths: CatalogScan(records=(), skipped_files=()),
        )
        blueprint_file = workspace_paths.catalog_blueprints_dir / "demo.yml"
        blueprint_file.chmod(0o000)
        try:
            result = catalog.show("demo", CatalogItemType.BLUEPRINT)
        finally:
            blueprint_file.chmod(0o644)

        assert result.error_code == ErrorCode.CATALOG_FILE_UNREADABLE
        assert result.errors == [
            f"Failed to read file for catalog blueprint 'demo': [Errno 13] Permission denied: '{blueprint_file}'"
        ]
        assert result.diagnostics == []
        assert frozen_diagnostic_clock.exists() is False


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
        result = catalog.delete(CatalogItemType.BLUEPRINT, "user-only")

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
    if case == "tier_conflict":
        return catalog.create(CatalogItemType.BLUEPRINT, "demo", user=True, global_=True)
    if case == "collision":
        catalog.create(CatalogItemType.BLUEPRINT, "demo")
        return catalog.create(CatalogItemType.BLUEPRINT, "demo")
    if case == "reindex_miss":
        monkeypatch.setattr(
            "dovo.core.catalog.services.inventory.scan_and_index_tier",
            lambda tier, paths: CatalogScan(records=(), skipped_files=()),
        )
        return catalog.create(CatalogItemType.BLUEPRINT, "demo")
    if case == "protected_delete":
        return catalog.delete(CatalogItemType.BLUEPRINT, "dovo/fix-tests")
    if case == "tier_delete":
        _write_user_blueprint("user-only")
        return catalog.delete(CatalogItemType.BLUEPRINT, "user-only")
    return catalog.delete(CatalogItemType.BLUEPRINT, "missing")


class CatalogMutationOutcomeTests:
    """Tests verifying create/delete classify business failures and let everything else propagate."""

    @pytest.mark.parametrize(
        ("case", "expected_code"),
        [
            pytest.param("tier_conflict", ErrorCode.CATALOG_TIER_CONFLICT, id="tier_conflict"),
            pytest.param("collision", ErrorCode.CATALOG_ALREADY_EXISTS, id="already_exists"),
            pytest.param("reindex_miss", ErrorCode.CATALOG_WRITE_FAILED, id="write_failed"),
            pytest.param("not_found", ErrorCode.CATALOG_NOT_FOUND, id="not_found"),
            pytest.param("protected_delete", ErrorCode.CATALOG_PROTECTED, id="protected"),
            pytest.param("tier_delete", ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN, id="wrong_tier"),
        ],
    )
    def test_classified_failure_returns_error_code(
        self,
        workspace_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        case: str,
        expected_code: ErrorCode,
    ) -> None:
        """Return error_code == expected_code with one entry in errors, and leave <DOVO_HOME>/logs absent."""
        result = _run_classified_case(Catalog(workspace_paths), monkeypatch, case)

        assert result.error_code == expected_code
        assert len(result.errors) == 1
        assert result.ok is False
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_delete_success_result_is_ok(self, workspace_paths: WorkspacePaths) -> None:
        """Return error_code None, errors [], deleted True, cancelled False, item.key 'demo' and ok True for a deleted REPO blueprint."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")

        result = catalog.delete(CatalogItemType.BLUEPRINT, "demo")

        assert result.error_code is None
        assert result.errors == []
        assert result.deleted is True
        assert result.cancelled is False
        assert result.item is not None
        assert result.item.key == "demo"
        assert result.ok is True

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
                catalog.delete(CatalogItemType.BLUEPRINT, "demo")
            else:
                catalog.create(CatalogItemType.BLUEPRINT, "demo")

        assert not resolve_global_paths(None).logs_dir.exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    @pytest.mark.parametrize(
        ("operation", "message_start"),
        [
            pytest.param("create", "Failed to write catalog item '", id="create"),
            pytest.param("delete", "Failed to delete catalog item '", id="delete"),
        ],
    )
    def test_write_failure_is_catalog_write_failed_in_diagnostics(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, operation: str, message_start: str
    ) -> None:
        """On a read-only blueprints dir, return CATALOG_WRITE_FAILED with the record in diagnostics and one catalog.<operation> event; create leaves no file, delete keeps it."""
        catalog = Catalog(workspace_paths)
        blueprints_dir = workspace_paths.catalog_blueprints_dir
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        if operation == "delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")
        blueprints_dir.chmod(0o555)
        try:
            result = (
                catalog.delete(CatalogItemType.BLUEPRINT, "demo")
                if operation == "delete"
                else catalog.create(CatalogItemType.BLUEPRINT, "demo")
            )
        finally:
            blueprints_dir.chmod(0o755)

        assert result.error_code == ErrorCode.CATALOG_WRITE_FAILED
        assert len(result.errors) == 1
        assert result.errors[0].startswith(message_start)
        assert "Details logged" not in result.errors[0]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == f"catalog.{operation}"
        assert event["exception_type"] == "PermissionError"
        if operation == "delete":
            assert (blueprints_dir / "demo.yml").is_file()
        else:
            assert sorted(path.name for path in blueprints_dir.iterdir()) == []

    @pytest.mark.parametrize(
        ("operation", "patched"),
        [
            pytest.param("create", "dovo.core.catalog.services.inventory.scan_and_index_tier", id="create"),
            pytest.param("delete", "dovo.core.catalog.services.inventory.delete_file", id="delete"),
        ],
    )
    def test_unexpected_failure_is_internal_error_and_recorded(
        self,
        workspace_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        operation: str,
        patched: str,
    ) -> None:
        """With scan_and_index_tier/delete_file raising TypeError("bad"), return INTERNAL_ERROR with the record in diagnostics, and one catalog.<operation> event."""
        catalog = Catalog(workspace_paths)
        if operation == "delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")

        def _explode(*args: object) -> None:
            raise TypeError("bad")

        monkeypatch.setattr(patched, _explode)

        result = (
            catalog.delete(CatalogItemType.BLUEPRINT, "demo")
            if operation == "delete"
            else catalog.create(CatalogItemType.BLUEPRINT, "demo")
        )

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == [f"Unexpected error during catalog.{operation}: bad"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == f"catalog.{operation}"
        assert event["exception_type"] == "TypeError"


class CatalogDeleteItemTypeTests:
    """Tests verifying Catalog.delete resolves sha_or_name among items of the requested type only."""

    @pytest.mark.parametrize(
        ("item_type", "other_type"),
        [
            pytest.param(CatalogItemType.BLUEPRINT, CatalogItemType.STEP, id="blueprint"),
            pytest.param(CatalogItemType.STEP, CatalogItemType.BLUEPRINT, id="step"),
        ],
    )
    @pytest.mark.parametrize("by", ["name", "sha"])
    def test_other_type_match_is_not_found_and_never_confirms(
        self, workspace_paths: WorkspacePaths, item_type: CatalogItemType, other_type: CatalogItemType, by: str
    ) -> None:
        """Return CATALOG_NOT_FOUND naming item_type, never call confirm, and keep the other type's file."""
        catalog = Catalog(workspace_paths)
        created = catalog.create(other_type, "other")
        assert created.item is not None
        target = created.item.sha if by == "sha" else "other"
        asked: list[CatalogRecord] = []

        result = catalog.delete(item_type, target, confirm=lambda item: asked.append(item) is None)

        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.errors == [f"Catalog {item_type.value} '{target}' not found."]
        assert asked == []
        assert created.resolved_path is not None
        assert created.resolved_path.is_file()


class CatalogDeleteConfirmTests:
    """Tests verifying Catalog.delete resolves the name before it confirms, and builds a declined result itself."""

    def test_delete_asks_confirm_once_and_removes(self, workspace_paths: WorkspacePaths) -> None:
        """Call confirm once with the 'demo' record, then return deleted True, cancelled False, error_code None and remove the file."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")
        asked: list[CatalogRecord] = []

        def confirm(item: CatalogRecord) -> bool:
            asked.append(item)
            return True

        result = catalog.delete(CatalogItemType.BLUEPRINT, "demo", confirm=confirm)

        assert [item.key for item in asked] == ["demo"]
        assert result.deleted is True
        assert result.cancelled is False
        assert result.error_code is None
        assert not (workspace_paths.catalog_blueprints_dir / "demo.yml").exists()

    def test_declined_confirm_returns_cancelled_and_keeps_file(self, workspace_paths: WorkspacePaths) -> None:
        """Return the declined payload (cancelled True, item the kept 'demo' record, item_type blueprint, no error_code) with ok True, and keep the file, when confirm returns False."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")

        result = catalog.delete(CatalogItemType.BLUEPRINT, "demo", confirm=lambda item: False)

        assert result.model_dump(mode="json") == {
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "error_code": None,
            "item_type": "blueprint",
            "item": {
                "key": "demo",
                "sha": result.item.sha if result.item is not None else None,
                "item_type": "blueprint",
                "name": "demo",
                "namespace": None,
                "path": "blueprints/demo.yml",
                "checksum": result.item.checksum if result.item is not None else None,
                "tier": "repo",
            },
            "deleted": False,
            "cancelled": True,
        }
        assert result.ok is True
        assert (workspace_paths.catalog_blueprints_dir / "demo.yml").is_file()

    @pytest.mark.parametrize(
        ("case", "expected_code"),
        [
            pytest.param("not_found", ErrorCode.CATALOG_NOT_FOUND, id="not_found"),
            pytest.param("protected_delete", ErrorCode.CATALOG_PROTECTED, id="protected"),
            pytest.param("tier_delete", ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN, id="wrong_tier"),
        ],
    )
    def test_refused_target_never_confirms(
        self, workspace_paths: WorkspacePaths, case: str, expected_code: ErrorCode
    ) -> None:
        """Return error_code == expected_code and never call confirm for an unknown, protected or user-tier name."""
        names = {"not_found": "missing", "protected_delete": "dovo/fix-tests", "tier_delete": "user-only"}
        _write_user_blueprint("user-only")
        asked: list[CatalogRecord] = []

        result = Catalog(workspace_paths).delete(
            CatalogItemType.BLUEPRINT, names[case], confirm=lambda item: asked.append(item) is None
        )

        assert result.error_code == expected_code
        assert asked == []

    def test_file_removed_after_confirmation_returns_not_found(self, workspace_paths: WorkspacePaths) -> None:
        """Return CATALOG_NOT_FOUND, errors == ["Catalog blueprint 'demo' not found."] and deleted False when the file is removed after confirm."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")
        demo_file = workspace_paths.catalog_blueprints_dir / "demo.yml"

        def confirm(item: CatalogRecord) -> bool:
            demo_file.unlink()
            return True

        result = catalog.delete(CatalogItemType.BLUEPRINT, "demo", confirm=confirm)

        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.errors == ["Catalog blueprint 'demo' not found."]
        assert result.deleted is False

    def test_lock_timeout_after_confirmation_propagates(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError("lock held") when the lock times out after confirm returned True, and keep the file."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "demo")

        def confirm(item: CatalogRecord) -> bool:
            monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)
            return True

        with pytest.raises(LockTimeoutError, match=r"^lock held$"):
            catalog.delete(CatalogItemType.BLUEPRINT, "demo", confirm=confirm)

        assert (workspace_paths.catalog_blueprints_dir / "demo.yml").is_file()


class CatalogListTests:
    """Tests verifying Catalog.list reports skipped files as warnings and copies a caught failure into its result."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_unreadable_file_is_a_warning_and_others_listed(self, workspace_paths: WorkspacePaths) -> None:
        """Return items containing 'good' and the unreadable file's read error in warnings, with errors []."""
        catalog = Catalog(workspace_paths)
        catalog.create(CatalogItemType.BLUEPRINT, "good")
        catalog.create(CatalogItemType.BLUEPRINT, "bad")
        bad_file = workspace_paths.catalog_blueprints_dir / "bad.yml"
        bad_file.chmod(0o000)
        try:
            result = catalog.list(CatalogItemType.BLUEPRINT)
        finally:
            bad_file.chmod(0o644)

        keys = [item.key for item in result.items]
        assert "good" in keys
        assert "bad" not in keys
        assert len(result.warnings) == 1
        assert result.warnings[0].startswith(f"Failed to read catalog blueprint '{bad_file}': ")
        assert result.errors == []

    def test_list_failure_copies_error_and_record(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """With atomic_write_json raising OSError("disk full"), return CATALOG_WRITE_FAILED with the index-write message alone and its record in diagnostics, and one catalog.list event."""

        def _disk_full(path: Path, data: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_json", _disk_full)

        result = Catalog(workspace_paths).list(CatalogItemType.BLUEPRINT)

        index_path = workspace_paths.global_paths.global_catalog_dir / "index.json"
        assert result.error_code == ErrorCode.CATALOG_WRITE_FAILED
        assert result.errors == [f"Failed to write catalog index '{index_path}': disk full"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "catalog.list"

    def test_unknown_item_type_is_catalog_type_invalid(self, workspace_paths: WorkspacePaths) -> None:
        """Raise CatalogError CATALOG_TYPE_INVALID with coerce_catalog_item_type's message for get("x", item_type="widget")."""
        with pytest.raises(CatalogError) as raised:
            Catalog(workspace_paths).get("x", item_type="widget")

        assert raised.value.error_code == ErrorCode.CATALOG_TYPE_INVALID
        assert raised.value.message == "Invalid item_type 'widget'. Allowed choices: blueprint, step"


class CatalogFailureDiagnosticsTests:
    """Tests verifying a catalog failure with no written record reports an empty diagnostics list."""

    def test_unrecorded_unexpected_failure_has_empty_diagnostics(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With <DOVO_HOME>/logs a regular file, return INTERNAL_ERROR, errors ["Unexpected error during catalog.create: bad"] and diagnostics []."""
        logs_dir = resolve_global_paths(None).logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        def _explode(*args: object) -> None:
            raise TypeError("bad")

        monkeypatch.setattr("dovo.core.catalog.services.inventory.scan_and_index_tier", _explode)

        result = Catalog(workspace_paths).create(CatalogItemType.BLUEPRINT, "demo")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during catalog.create: bad"]
        assert result.diagnostics == []

    def test_not_found_has_empty_diagnostics(self, workspace_paths: WorkspacePaths) -> None:
        """Return CATALOG_NOT_FOUND for show('missing') with diagnostics []."""
        result = Catalog(workspace_paths).show("missing", CatalogItemType.BLUEPRINT)

        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.diagnostics == []


def _failing_result(catalog: Catalog, item_type: CatalogItemType, command: str) -> CatalogResult:
    """Drive command into a failure for item_type and return its result."""
    if command == "list":
        return catalog.list(item_type)
    if command == "show":
        return catalog.show("ghost", item_type)
    if command == "create":
        return catalog.create(item_type, "both", user=True, global_=True)
    if command == "delete":
        return catalog.delete(item_type, "ghost")
    return catalog.validate("ghost", item_type=item_type, blueprint_cls=BlueprintDefinition, step_cls=StepDefinition)


class CatalogResultItemTypeTests:
    """Tests verifying every catalog result names the item type it was built for."""

    @pytest.mark.parametrize("item_type", [CatalogItemType.BLUEPRINT, CatalogItemType.STEP])
    @pytest.mark.parametrize("command", ["list", "show", "create", "delete", "validate"])
    def test_failure_carries_requested_item_type(
        self,
        workspace_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        item_type: CatalogItemType,
        command: str,
    ) -> None:
        """Return an error_code with item_type == the requested type for a failed list, show, create, delete or validate."""
        if command == "list":
            monkeypatch.setattr(Filesystem, "atomic_write_json", _raise_disk_full)

        result = _failing_result(Catalog(workspace_paths), item_type, command)

        assert result.error_code is not None
        assert result.item_type is item_type

    def test_tier_conflict_writes_nothing_and_records_no_diagnostic(self, workspace_paths: WorkspacePaths) -> None:
        """Return CATALOG_TIER_CONFLICT with its message, remediation, item_type step and diagnostics [], write demo.yml to no tier, and leave <DOVO_HOME>/logs absent."""
        result = Catalog(workspace_paths).create(CatalogItemType.STEP, "demo", user=True, global_=True)

        assert result.model_dump(mode="json") == {
            "errors": ["--user and --global are mutually exclusive."],
            "warnings": [],
            "remediations": [{"message": "Pass at most one of --user and --global.", "command": None}],
            "diagnostics": [],
            "error_code": "CATALOG_TIER_CONFLICT",
            "item_type": "step",
            "item": None,
            "resolved_path": None,
        }
        global_paths = resolve_global_paths(None)
        for catalog_dir in (
            workspace_paths.catalog_dir,
            global_paths.user_catalog_dir,
            global_paths.global_catalog_dir,
        ):
            assert not (catalog_dir / "steps" / "demo.yml").exists()
        assert not global_paths.logs_dir.exists()

    @pytest.mark.parametrize(
        "result_cls",
        [CatalogListResult, CatalogShowResult, CatalogCreateResult, CatalogDeleteResult],
    )
    def test_result_without_item_type_is_rejected(self, result_cls: type[CatalogResult]) -> None:
        """Raise ValidationError naming item_type when a catalog result is built without one."""
        with pytest.raises(ValidationError, match="item_type"):
            result_cls.model_validate({})

    def test_validate_result_without_item_type_is_rejected(self) -> None:
        """Raise ValidationError naming item_type when a CatalogValidateResult is built without one."""
        with pytest.raises(ValidationError, match="item_type"):
            CatalogValidateResult.model_validate({"target": "demo"})


def _raise_disk_full(path: Path, data: object) -> None:
    """Stand in for Filesystem.atomic_write_json, failing as a full disk would."""
    raise OSError("disk full")
