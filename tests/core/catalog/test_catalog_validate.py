"""Tier 1 domain contract tests for Catalog.validate (dovo blueprint validate / dovo step validate)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition, StepDefinition
from dovo.core.catalog.models import CatalogItemType, CatalogValidateResult, CatalogValidateStatus
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.workspace_paths import initialized_workspace_paths


def _validate(
    target: str, *, path: Path, item_type: CatalogItemType | None = None, catalog: Catalog | None = None
) -> CatalogValidateResult:
    """Call Catalog.validate with the real BlueprintDefinition/StepDefinition schema classes."""
    cat = catalog if catalog is not None else Catalog(initialized_workspace_paths(path))
    return cat.validate(
        target,
        item_type=item_type,
        blueprint_cls=BlueprintDefinition,
        step_cls=StepDefinition,
    )


def _write_yaml(path: Path, payload: dict[str, object]) -> Path:
    """Write payload as YAML to path, preserving key order, and return path."""
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


class CatalogValidateServiceTests:
    """Tier 1 domain contract tests for Catalog.validate."""

    def test_validate_direct_file_path_resolves_and_validates(self, isolated_workspace: Path) -> None:
        target_path = _write_yaml(
            isolated_workspace / "external.yml",
            {"name": "external", "steps": [{"id": "s1", "run": "echo hi"}]},
        )

        result = _validate(str(target_path), path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.OK
        assert result.valid is True
        assert result.target == str(target_path)
        assert result.resolved_path == target_path
        assert result.item_type == "blueprint"
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []

    def test_validate_catalog_name_resolves_via_get(self, isolated_workspace: Path) -> None:
        write_runnable_blueprint(isolated_workspace, key="sample-flow", steps=[{"id": "s1", "run": "echo hi"}])

        result = _validate("sample-flow", path=isolated_workspace)

        assert result.status == CatalogValidateStatus.OK
        assert result.valid is True
        assert result.target == "sample-flow"
        assert result.resolved_path == isolated_workspace / ".dovo" / "catalog" / "blueprints" / "sample-flow.yml"
        assert result.item_type == "blueprint"
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []

    def test_validate_unknown_name_returns_not_found(self, isolated_workspace: Path) -> None:
        result = _validate("nonexistent-flow", path=isolated_workspace)

        assert result.status == CatalogValidateStatus.NOT_FOUND
        assert result.valid is False
        assert result.target == "nonexistent-flow"
        assert result.resolved_path is None
        assert result.item_type is None
        assert result.errors == ["Catalog item 'nonexistent-flow' not found (CATALOG_ITEM_NOT_FOUND)."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(
                message="Check that 'nonexistent-flow' names an indexed catalog item, or pass a file path instead."
            )
        ]

    def test_validate_file_target_without_type_returns_type_required(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        draft_path = _write_yaml(isolated_workspace / "draft.yml", {"name": "draft"})
        catalog = Catalog(initialized_workspace_paths(isolated_workspace))

        def _fail_read_text(self: Path, *args: object, **kwargs: object) -> str:
            raise AssertionError("draft.yml contents must not be read before --type is validated")

        monkeypatch.setattr(Path, "read_text", _fail_read_text)

        result = _validate("draft.yml", path=isolated_workspace, item_type=None, catalog=catalog)

        assert result.status == CatalogValidateStatus.TYPE_REQUIRED
        assert result.valid is False
        assert result.target == "draft.yml"
        assert result.resolved_path == draft_path
        assert result.item_type is None
        assert result.errors == ["'--type' is required to validate file target 'draft.yml' (CATALOG_TYPE_REQUIRED)."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Pass '--type blueprint' or '--type step' for a file target.")
        ]

    def test_validate_unreadable_file_returns_unreadable_status(self, isolated_workspace: Path) -> None:
        target_path = _write_yaml(isolated_workspace / "noperm.yml", {"name": "noperm"})
        target_path.chmod(0)
        try:
            if os.access(target_path, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")

            result = _validate("noperm.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)
        finally:
            target_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert result.status == CatalogValidateStatus.UNREADABLE
        assert result.valid is False
        assert result.target == "noperm.yml"
        assert result.resolved_path == target_path
        assert result.item_type == "blueprint"
        assert result.errors == [
            f"Failed to read '{target_path}': [Errno 13] Permission denied: '{target_path}' (CATALOG_FILE_UNREADABLE)."
        ]
        assert result.warnings == []
        assert result.remediations == [Remediation(message=f"Check that '{target_path}' exists and is readable.")]

    def test_validate_yaml_syntax_error_reports_line_and_column(self, isolated_workspace: Path) -> None:
        target_path = isolated_workspace / "bad.yml"
        target_path.write_text("foo: bar: baz\n", encoding="utf-8")

        result = _validate("bad.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.SYNTAX_ERROR
        assert result.valid is False
        assert len(result.errors) == 1
        assert "CATALOG_YAML_SYNTAX_ERROR" in result.errors[0]
        assert "line 1, column 9" in result.errors[0]

    def test_validate_non_mapping_root_returns_invalid(self, isolated_workspace: Path) -> None:
        target_path = isolated_workspace / "list.yml"
        target_path.write_text("- a\n- b\n", encoding="utf-8")

        result = _validate("list.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert result.target == "list.yml"
        assert result.resolved_path == target_path
        assert result.item_type == "blueprint"
        assert result.errors == [f"'{target_path}' root content must be a YAML mapping (CATALOG_ROOT_NOT_OBJECT)."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message=f"Rewrite '{target_path}' with a top-level YAML mapping (key: value pairs).")
        ]

    def test_validate_schema_invalid_blueprint_reports_field_paths(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "bad_timeout.yml",
            {"name": "bad-timeout", "timeout_seconds": "not-a-number", "steps": [{"id": "s1", "run": "echo hi"}]},
        )

        result = _validate("bad_timeout.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert len(result.errors) == 1
        assert "timeout_seconds" in result.errors[0]
        assert "CATALOG_SCHEMA_INVALID" in result.errors[0]

    def test_validate_step_shape_conflict_surfaces_as_schema_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "conflict.yml",
            {"id": "s1", "run": "pytest", "uses": "dovo/ai-code-patcher"},
        )

        result = _validate("conflict.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert len(result.errors) == 1
        assert "run" in result.errors[0]
        assert "uses" in result.errors[0]
        assert "CATALOG_SCHEMA_INVALID" in result.errors[0]

    def test_validate_step_target_uses_step_schema(self, isolated_workspace: Path) -> None:
        _write_yaml(isolated_workspace / "missing_id.yml", {"run": "pytest"})

        result = _validate("missing_id.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert result.target == "missing_id.yml"
        assert result.resolved_path == isolated_workspace / "missing_id.yml"
        assert result.item_type == "step"
        assert result.errors == ["id: Field required (CATALOG_SCHEMA_INVALID)."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Fix the reported schema error for target 'missing_id'.")]

    def test_validate_duplicate_step_id_reports_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "dup.yml",
            {"name": "d", "steps": [{"id": "run-tests", "run": "pytest"}, {"id": "run-tests", "run": "pytest -x"}]},
        )

        result = _validate("dup.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert result.target == "dup.yml"
        assert result.resolved_path == isolated_workspace / "dup.yml"
        assert result.item_type == "blueprint"
        assert result.errors == ["Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(
                message="Resolve: Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."
            )
        ]
        assert len(result.remediations) == len(result.errors)

    def test_validate_duplicate_step_id_in_nested_loop_reports_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "loopdup.yml",
            {
                "name": "loop-dup",
                "steps": [
                    {
                        "id": "loop-1",
                        "type": "loop",
                        "until": ["steps.inner.exit_code == 0"],
                        "do": [
                            {"id": "inner", "run": "echo 1"},
                            {"id": "inner", "run": "echo 2"},
                        ],
                    }
                ],
            },
        )

        result = _validate("loopdup.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert result.errors == ["Step id 'inner' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."]

    def test_validate_duplicate_input_alias_reports_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "alias.yml",
            {
                "name": "alias-dup",
                "inputs": {
                    "foo": {"type": "string", "aliases": ["-f"]},
                    "bar": {"type": "string", "aliases": ["-f"]},
                },
                "steps": [{"id": "s1", "run": "echo hi"}],
            },
        )

        result = _validate("alias.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert result.target == "alias.yml"
        assert result.resolved_path == isolated_workspace / "alias.yml"
        assert result.item_type == "blueprint"
        assert result.errors == ["Alias '-f' is declared by both 'foo' and 'bar' (CATALOG_DUPLICATE_INPUT_ALIAS)."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(
                message="Resolve: Alias '-f' is declared by both 'foo' and 'bar' (CATALOG_DUPLICATE_INPUT_ALIAS)."
            )
        ]

    def test_validate_undeclared_placeholder_reports_warning(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "ph.yml",
            {"name": "ph", "steps": [{"id": "s1", "run": "echo ${{ inputs.missing }}"}]},
        )

        result = _validate("ph.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.OK
        assert result.valid is True
        assert result.target == "ph.yml"
        assert result.resolved_path == isolated_workspace / "ph.yml"
        assert result.item_type == "blueprint"
        assert result.errors == []
        assert result.warnings == [
            "Placeholder 'inputs.missing' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER)."
        ]
        assert result.remediations == []

    def test_validate_unsafe_script_path_reports_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "script.yml",
            {"name": "s", "steps": [{"id": "s1", "type": "script", "script_path": "../escape.sh"}]},
        )

        result = _validate("script.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert result.valid is False
        assert result.target == "script.yml"
        assert result.resolved_path == isolated_workspace / "script.yml"
        assert result.item_type == "blueprint"
        assert result.errors == [
            "Step 's1' script_path '../escape.sh' is not a safe relative path (CATALOG_UNSAFE_SCRIPT_PATH)."
        ]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(
                message="Resolve: Step 's1' script_path '../escape.sh' is not a safe relative path (CATALOG_UNSAFE_SCRIPT_PATH)."
            )
        ]

    def test_validate_missing_script_file_reports_warning(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "missing_script.yml",
            {"name": "s", "steps": [{"id": "s1", "type": "script", "script_path": "scripts/none.sh"}]},
        )

        result = _validate("missing_script.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.OK
        assert result.valid is True
        assert result.target == "missing_script.yml"
        assert result.resolved_path == isolated_workspace / "missing_script.yml"
        assert result.item_type == "blueprint"
        assert result.errors == []
        assert result.warnings == [
            "Step 's1' script_path 'scripts/none.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."
        ]
        assert result.remediations == []

    def test_validate_valid_blueprint_returns_ok_status(self, isolated_workspace: Path) -> None:
        write_runnable_blueprint(isolated_workspace, key="clean-flow", steps=[{"id": "s1", "run": "echo hi"}])

        result = _validate("clean-flow", path=isolated_workspace)

        assert result.status == CatalogValidateStatus.OK
        assert result.valid is True
        assert result.errors == []
        assert result.ok is True

    def test_validate_result_ok_property_requires_valid_and_no_errors(self) -> None:
        result = CatalogValidateResult(
            status=CatalogValidateStatus.OK,
            valid=True,
            target="x",
            errors=["boom"],
        )

        assert result.ok is False


class CatalogValidateAskTests:
    """Tier 1 integration tests for Catalog.validate over `type: ask` steps."""

    def test_ask_rule_violation_reports_ask_invalid_with_step_id(self, isolated_workspace: Path) -> None:
        target_path = _write_yaml(
            isolated_workspace / "ask.yml", {"name": "ask", "steps": [{"id": "approve", "type": "ask"}]}
        )

        result = _validate(str(target_path), path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert "Step 'approve'" in result.errors[0]
        assert result.errors[0].endswith("(CATALOG_ASK_INVALID).")

    def test_unknown_interaction_mode_stays_schema_invalid(self, isolated_workspace: Path) -> None:
        steps = [{"id": "approve", "type": "ask", "interaction": {"mode": "dropdown", "message": "Go?"}}]
        target_path = _write_yaml(isolated_workspace / "ask.yml", {"name": "ask", "steps": steps})

        result = _validate(str(target_path), path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert result.status == CatalogValidateStatus.INVALID
        assert all(error.endswith("(CATALOG_SCHEMA_INVALID).") for error in result.errors)


class _ExplodingBlueprintDefinition(BlueprintDefinition):
    """BlueprintDefinition whose document loading fails with an unexpected RuntimeError."""

    @classmethod
    def from_document(cls, raw: dict[str, object], *, key: str) -> BlueprintDefinition:
        raise RuntimeError("boom")


class CatalogValidateDiagnosticLogTests:
    """Tests verifying an unexpected schema-check failure is recorded and named in the validate result."""

    def test_unexpected_schema_failure_records_error_and_appends_hint(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return valid False with '<exc> (CATALOG_SCHEMA_INVALID).' plus the hint when blueprint_cls.from_document raises RuntimeError, and record operation catalog.validate_definition."""
        target_path = _write_yaml(
            workspace_paths.root_dir / "demo.yml", {"name": "demo", "steps": [{"id": "s1", "run": "echo hi"}]}
        )

        result = Catalog(workspace_paths).validate(
            str(target_path),
            item_type=CatalogItemType.BLUEPRINT,
            blueprint_cls=_ExplodingBlueprintDefinition,
            step_cls=StepDefinition,
        )

        assert result.valid is False
        assert result.errors == [
            f"boom (CATALOG_SCHEMA_INVALID).\nDetails logged: {frozen_diagnostic_clock} (event {FROZEN_EVENT_ID})"
        ]
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "error"
        assert event["operation"] == "catalog.validate_definition"
        assert event["context"] == {"key": "demo"}
        assert event["exception_type"] == "RuntimeError"
