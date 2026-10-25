"""Integration tests for catalog scaffolding, protection, discovery fallback, and collisions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.catalog import Catalog, CatalogError
from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.catalog.models import (
    CatalogCreateResult,
    CatalogDeleteResult,
    CatalogItemType,
    CatalogScan,
    CatalogTier,
)
from dovo.core.catalog.services.inventory import compute_catalog_sha
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
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
        ("content", "code", "message_start"),
        [
            pytest.param(
                "- a\n- b\n",
                ErrorCode.CATALOG_SYNTAX_ERROR,
                "Failed to load catalog blueprint 'blueprints/bad.yml': invalid or non-object YAML content.",
                id="non_mapping",
            ),
            pytest.param(
                "name: bad\nsteps: [\n",
                ErrorCode.CATALOG_SYNTAX_ERROR,
                "Failed to load catalog blueprint 'blueprints/bad.yml'",
                id="syntax",
            ),
            pytest.param(
                "name: bad\ntimeout_seconds: x\nsteps: []\n",
                ErrorCode.CATALOG_SCHEMA_INVALID,
                "Model validation failed for 'bad': Blueprint definition validation failed:",
                id="schema",
            ),
        ],
    )
    def test_get_unloadable_definition_raises_classified_error(
        self, workspace_paths: WorkspacePaths, content: str, code: ErrorCode, message_start: str
    ) -> None:
        """Raise CatalogError with error_code == code and message starting message_start, and leave <DOVO_HOME>/logs absent."""
        blueprint_file = workspace_paths.catalog_blueprints_dir / "bad.yml"
        blueprint_file.parent.mkdir(parents=True, exist_ok=True)
        blueprint_file.write_text(content, encoding="utf-8")

        with pytest.raises(CatalogError) as raised:
            Catalog(workspace_paths).get("bad", item_type=CatalogItemType.BLUEPRINT, definition_cls=BlueprintDefinition)

        assert raised.value.error_code == code
        assert raised.value.message.startswith(message_start)
        assert not resolve_global_paths(None).logs_dir.exists()

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
        result = Catalog(workspace_paths).show("ghost")

        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.errors == ["Catalog item 'ghost' not found."]
        assert result.item is None
        assert result.ok is False

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_show_unreadable_file_records_cause_and_appends_hint(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Return error_code CATALOG_FILE_UNREADABLE and the read error plus hint, with one error event under operation catalog.show."""
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
            result = catalog.show("demo")
        finally:
            blueprint_file.chmod(0o644)

        event = read_single_event(frozen_diagnostic_clock)
        assert result.error_code == ErrorCode.CATALOG_FILE_UNREADABLE
        assert result.errors == [
            f"Failed to read file for catalog blueprint 'demo': {event['exception_message']}"
            f"{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        assert event["level"] == "error"
        assert event["operation"] == "catalog.show"
        assert event["context"] == {"name": "demo"}
        assert event["exception_type"] == "PermissionError"


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
            "dovo.core.catalog.services.inventory.scan_and_index_tier",
            lambda tier, paths: CatalogScan(records=(), skipped_files=()),
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
        ("case", "expected_code"),
        [
            pytest.param("invalid_type", ErrorCode.CATALOG_TYPE_INVALID, id="invalid_type"),
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

        result = catalog.delete("demo")

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
                catalog.delete("demo")
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
    def test_write_failure_is_catalog_write_failed_with_hint(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, operation: str, message_start: str
    ) -> None:
        """On a read-only blueprints dir, return CATALOG_WRITE_FAILED with the hint and one catalog.<operation> event; create leaves no file, delete keeps it."""
        catalog = Catalog(workspace_paths)
        blueprints_dir = workspace_paths.catalog_blueprints_dir
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        if operation == "delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")
        blueprints_dir.chmod(0o555)
        try:
            result = (
                catalog.delete("demo") if operation == "delete" else catalog.create(CatalogItemType.BLUEPRINT, "demo")
            )
        finally:
            blueprints_dir.chmod(0o755)

        assert result.error_code == ErrorCode.CATALOG_WRITE_FAILED
        assert len(result.errors) == 1
        assert result.errors[0].startswith(message_start)
        assert result.errors[0].endswith(diagnostic_hint(frozen_diagnostic_clock))
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
        """With scan_and_index_tier/delete_file raising TypeError("bad"), return INTERNAL_ERROR with the hint, and one catalog.<operation> event."""
        catalog = Catalog(workspace_paths)
        if operation == "delete":
            catalog.create(CatalogItemType.BLUEPRINT, "demo")

        def _explode(*args: object) -> None:
            raise TypeError("bad")

        monkeypatch.setattr(patched, _explode)

        result = catalog.delete("demo") if operation == "delete" else catalog.create(CatalogItemType.BLUEPRINT, "demo")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == [
            f"Unexpected error during catalog.{operation}: bad{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == f"catalog.{operation}"
        assert event["exception_type"] == "TypeError"


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
            result = catalog.list(type_filter=CatalogItemType.BLUEPRINT)
        finally:
            bad_file.chmod(0o644)

        keys = [item.key for item in result.items]
        assert "good" in keys
        assert "bad" not in keys
        assert len(result.warnings) == 1
        assert result.warnings[0].startswith(f"Failed to read catalog blueprint '{bad_file}': ")
        assert result.errors == []

    def test_list_failure_copies_error_with_hint(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """With atomic_write_json raising OSError("disk full"), return CATALOG_WRITE_FAILED with the index-write message plus hint, and one catalog.list event."""

        def _disk_full(path: Path, data: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_json", _disk_full)

        result = Catalog(workspace_paths).list()

        index_path = workspace_paths.global_paths.global_catalog_dir / "index.json"
        assert result.error_code == ErrorCode.CATALOG_WRITE_FAILED
        assert result.errors == [
            f"Failed to write catalog index '{index_path}': disk full{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "catalog.list"

    def test_invalid_type_filter_is_catalog_type_invalid(self, workspace_paths: WorkspacePaths) -> None:
        """Return CATALOG_TYPE_INVALID with coerce_catalog_item_type's message for list(type_filter="widget"), and leave <DOVO_HOME>/logs absent."""
        result = Catalog(workspace_paths).list(type_filter="widget")

        assert result.error_code == ErrorCode.CATALOG_TYPE_INVALID
        assert result.errors == ["Invalid item_type 'widget'. Allowed choices: blueprint, step"]
        assert not resolve_global_paths(None).logs_dir.exists()
