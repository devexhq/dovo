"""Tier 1 domain contract tests for Catalog.validate (dovo blueprint validate / dovo step validate)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.catalog import Catalog, CatalogError
from dovo.core.catalog.definitions import BlueprintDefinition, StepDefinition
from dovo.core.catalog.models import CatalogItemType, CatalogValidateResult
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.diagnostic_log import diagnostic_record
from tests.harness.workspace_paths import initialized_workspace_paths


def _validate(
    target: str,
    *,
    path: Path,
    item_type: CatalogItemType = CatalogItemType.BLUEPRINT,
    catalog: Catalog | None = None,
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

        assert (result.error_code, result.ok) == (None, True)
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

        assert (result.error_code, result.ok) == (None, True)
        assert result.valid is True
        assert result.target == "sample-flow"
        assert result.resolved_path == isolated_workspace / ".dovo" / "catalog" / "blueprints" / "sample-flow.yml"
        assert result.item_type == "blueprint"
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []

    def test_validate_unknown_name_returns_not_found(self, isolated_workspace: Path) -> None:
        result = _validate("nonexistent-flow", path=isolated_workspace)

        assert result.ok is False
        assert result.valid is False
        assert result.target == "nonexistent-flow"
        assert result.resolved_path is None
        assert result.item_type is None
        assert result.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert result.errors == ["Catalog item 'nonexistent-flow' not found."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(
                message="Check that 'nonexistent-flow' names an indexed blueprint, or pass a file path instead."
            )
        ]

    def test_validate_unreadable_file_returns_file_unreadable(self, isolated_workspace: Path) -> None:
        target_path = _write_yaml(isolated_workspace / "noperm.yml", {"name": "noperm"})
        target_path.chmod(0)
        try:
            if os.access(target_path, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")

            result = _validate("noperm.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)
        finally:
            target_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_FILE_UNREADABLE, False)
        assert result.valid is False
        assert result.target == "noperm.yml"
        assert result.resolved_path == target_path
        assert result.item_type == "blueprint"
        assert result.errors == [f"Failed to read '{target_path}': [Errno 13] Permission denied: '{target_path}'."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message=f"Check that '{target_path}' exists and is readable.")]

    def test_validate_yaml_syntax_error_reports_line_and_column(self, isolated_workspace: Path) -> None:
        target_path = isolated_workspace / "bad.yml"
        target_path.write_text("foo: bar: baz\n", encoding="utf-8")

        result = _validate("bad.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SYNTAX_INVALID, False)
        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].startswith(f"YAML syntax error in '{target_path}' at line 1, column 9: ")

    def test_validate_non_mapping_root_returns_invalid(self, isolated_workspace: Path) -> None:
        target_path = isolated_workspace / "list.yml"
        target_path.write_text("- a\n- b\n", encoding="utf-8")

        result = _validate("list.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SYNTAX_INVALID, False)
        assert result.valid is False
        assert result.target == "list.yml"
        assert result.resolved_path == target_path
        assert result.item_type == "blueprint"
        assert result.errors == [f"'{target_path}' root content must be a YAML mapping."]
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

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].startswith("timeout_seconds: ")
        assert result.errors[0].endswith(".")
        assert not result.errors[0].endswith("..")

    def test_validate_step_shape_conflict_surfaces_as_schema_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "conflict.yml",
            {"id": "s1", "run": "pytest", "uses": "dovo/ai-code-patcher"},
        )

        result = _validate("conflict.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert result.errors == ["<root>: Value error, Step 's1': 'run' cannot be combined with uses."]

    def test_validate_step_target_uses_step_schema(self, isolated_workspace: Path) -> None:
        _write_yaml(isolated_workspace / "missing_id.yml", {"run": "pytest"})

        result = _validate("missing_id.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert result.target == "missing_id.yml"
        assert result.resolved_path == isolated_workspace / "missing_id.yml"
        assert result.item_type == "step"
        assert result.errors == ["id: Field required."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Fix the reported schema error for target 'missing_id'.")]

    def test_validate_duplicate_step_id_reports_error(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "dup.yml",
            {"name": "d", "steps": [{"id": "run-tests", "run": "pytest"}, {"id": "run-tests", "run": "pytest -x"}]},
        )

        result = _validate("dup.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert result.target == "dup.yml"
        assert result.resolved_path == isolated_workspace / "dup.yml"
        assert result.item_type == "blueprint"
        assert result.errors == ["Step id 'run-tests' is used by more than one step."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Resolve: Step id 'run-tests' is used by more than one step.")
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

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert result.errors == ["Step id 'inner' is used by more than one step."]

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

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert result.target == "alias.yml"
        assert result.resolved_path == isolated_workspace / "alias.yml"
        assert result.item_type == "blueprint"
        assert result.errors == ["Alias '-f' is declared by both 'foo' and 'bar'."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Resolve: Alias '-f' is declared by both 'foo' and 'bar'.")]

    def test_validate_undeclared_placeholder_reports_warning(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "ph.yml",
            {"name": "ph", "steps": [{"id": "s1", "run": "echo ${{ inputs.missing }}"}]},
        )

        result = _validate("ph.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert (result.error_code, result.ok) == (None, True)
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

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert result.valid is False
        assert result.target == "script.yml"
        assert result.resolved_path == isolated_workspace / "script.yml"
        assert result.item_type == "blueprint"
        assert result.errors == ["Step 's1' script_path '../escape.sh' is not a safe relative path."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Resolve: Step 's1' script_path '../escape.sh' is not a safe relative path.")
        ]

    def test_validate_missing_script_file_reports_warning(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "missing_script.yml",
            {"name": "s", "steps": [{"id": "s1", "type": "script", "script_path": "scripts/none.sh"}]},
        )

        result = _validate("missing_script.yml", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert (result.error_code, result.ok) == (None, True)
        assert result.valid is True
        assert result.target == "missing_script.yml"
        assert result.resolved_path == isolated_workspace / "missing_script.yml"
        assert result.item_type == "blueprint"
        assert result.errors == []
        assert result.warnings == [
            "Step 's1' script_path 'scripts/none.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."
        ]
        assert result.remediations == []

    def test_validate_valid_blueprint_returns_ok(self, isolated_workspace: Path) -> None:
        write_runnable_blueprint(isolated_workspace, key="clean-flow", steps=[{"id": "s1", "run": "echo hi"}])

        result = _validate("clean-flow", path=isolated_workspace)

        assert (result.error_code, result.ok) == (None, True)
        assert result.valid is True
        assert result.errors == []

    def test_validate_result_ok_follows_error_code(self) -> None:
        """Return ok False for a result with error_code CATALOG_SCHEMA_INVALID and ok True for one with error_code None and errors []."""
        failed = CatalogValidateResult(valid=False, target="x", error_code=ErrorCode.CATALOG_SCHEMA_INVALID)
        passed = CatalogValidateResult(valid=True, target="x", error_code=None, errors=[])

        assert failed.ok is False
        assert passed.ok is True

    def test_validate_catalog_name_is_scoped_to_item_type(self, isolated_workspace: Path) -> None:
        """For a REPO blueprint 'shared' and no step 'shared', return error_code CATALOG_NOT_FOUND, valid False and errors ["Catalog item 'shared' not found."] and a step-named remediation with item_type STEP, and error_code None with item_type BLUEPRINT."""
        write_runnable_blueprint(isolated_workspace, key="shared", steps=[{"id": "s1", "run": "echo hi"}])

        as_step = _validate("shared", path=isolated_workspace, item_type=CatalogItemType.STEP)
        as_blueprint = _validate("shared", path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert as_step.error_code == ErrorCode.CATALOG_NOT_FOUND
        assert as_step.valid is False
        assert as_step.errors == ["Catalog item 'shared' not found."]
        assert as_step.remediations == [
            Remediation(message="Check that 'shared' names an indexed step, or pass a file path instead.")
        ]
        assert as_blueprint.error_code is None

    @pytest.mark.parametrize(
        ("raised", "expected_code", "expected_errors", "logged"),
        [
            pytest.param(
                CatalogError(ErrorCode.CATALOG_WRITE_FAILED, "boom"),
                ErrorCode.CATALOG_WRITE_FAILED,
                ["boom"],
                False,
                id="write_failed",
            ),
            pytest.param(
                TypeError("bad"),
                ErrorCode.INTERNAL_ERROR,
                ["Unexpected error during catalog.validate: bad"],
                True,
                id="internal_error",
            ),
        ],
    )
    def test_validate_lookup_failure_is_classified(
        self,
        workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        raised: Exception,
        expected_code: ErrorCode,
        expected_errors: list[str],
        logged: bool,
    ) -> None:
        """With dovo.core.catalog.catalog.scan_and_index_catalog patched to raise `raised` for a catalog-name target, return error_code expected_code, valid False, resolved_path None; write_failed has errors ["boom"], internal_error has the unexpected-error message alone, its record in diagnostics and writes the catalog.validate event file."""

        def _explode(*args: object) -> None:
            raise raised

        monkeypatch.setattr("dovo.core.catalog.catalog.scan_and_index_catalog", _explode)

        result = _validate("demo", path=workspace_paths.root_dir, catalog=Catalog(workspace_paths))

        assert result.error_code == expected_code
        assert result.valid is False
        assert result.resolved_path is None
        assert result.errors == expected_errors
        assert result.diagnostics == ([diagnostic_record(frozen_diagnostic_clock)] if logged else [])
        assert frozen_diagnostic_clock.exists() is logged


_SEMANTIC_ERRORS: tuple[str, ...] = (
    "Step id 'a' is used by more than one step.",
    "Alias '-f' is declared by both 'foo' and 'bar'.",
)
_SEMANTIC_WARNINGS: tuple[str, ...] = (
    "Step 'a' script_path 'nope.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND).",
    "Placeholder 'inputs.ghost' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER).",
)


def _semantic_errors_and_warnings_blueprint() -> dict[str, object]:
    """Return a blueprint with a duplicate step id, a duplicate alias, a missing script and an undeclared placeholder."""
    return {
        "name": "w",
        "inputs": {
            "foo": {"type": "string", "aliases": ["-f"]},
            "bar": {"type": "string", "aliases": ["-f"]},
        },
        "steps": [
            {"id": "a", "run": "echo ${{ inputs.ghost }}"},
            {"id": "a", "type": "script", "script_path": "nope.sh"},
        ],
    }


class CatalogValidateFailurePathTests:
    """Tier 1 contract tests pinning every field of each failing Catalog.validate result."""

    def test_unreadable_file_records_no_diagnostic(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return CATALOG_FILE_UNREADABLE with empty diagnostics and write no event file for a mode-000 target."""
        target_path = _write_yaml(workspace_paths.root_dir / "noperm.yml", {"name": "noperm"})
        target_path.chmod(0)
        try:
            if os.access(target_path, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")
            result = _validate(str(target_path), path=workspace_paths.root_dir, catalog=Catalog(workspace_paths))
        finally:
            target_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert result.error_code == ErrorCode.CATALOG_FILE_UNREADABLE
        assert result.diagnostics == []
        assert frozen_diagnostic_clock.exists() is False

    def test_yaml_syntax_error_pins_every_field(self, isolated_workspace: Path) -> None:
        target_path = isolated_workspace / "bad.yml"
        target_path.write_text("foo: bar: baz\n", encoding="utf-8")

        result = _validate("bad.yml", path=isolated_workspace)

        assert result.model_dump(exclude={"errors"}) == {
            "error_code": ErrorCode.CATALOG_SYNTAX_INVALID,
            "warnings": [],
            "remediations": [
                {"message": f"Fix the YAML syntax error at line 1, column 9 in '{target_path}'.", "command": None}
            ],
            "diagnostics": [],
            "valid": False,
            "target": "bad.yml",
            "resolved_path": target_path,
            "item_type": "blueprint",
        }
        assert result.errors == [
            f"YAML syntax error in '{target_path}' at line 1, column 9: mapping values are not allowed here\n"
            '  in "<unicode string>", line 1, column 9:\n    foo: bar: baz\n            ^.'
        ]

    def test_several_schema_errors_keep_every_message_and_remediation(self, isolated_workspace: Path) -> None:
        _write_yaml(
            isolated_workspace / "multi.yml",
            {"name": "m", "timeout_seconds": "x", "description": 5, "steps": [{"id": "s1", "run": "echo"}]},
        )

        result = _validate("multi.yml", path=isolated_workspace)

        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.valid is False
        assert result.errors == [
            "description: Input should be a valid string.",
            "timeout_seconds: Input should be a valid integer, unable to parse string as an integer.",
        ]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Fix the reported schema error for target 'multi'.")] * 2
        assert result.diagnostics == []

    def test_several_semantic_errors_keep_their_warnings(self, isolated_workspace: Path) -> None:
        _write_yaml(isolated_workspace / "semantic.yml", _semantic_errors_and_warnings_blueprint())

        result = _validate("semantic.yml", path=isolated_workspace)

        assert result.model_dump() == {
            "error_code": ErrorCode.CATALOG_SCHEMA_INVALID,
            "errors": list(_SEMANTIC_ERRORS),
            "warnings": list(_SEMANTIC_WARNINGS),
            "remediations": [{"message": f"Resolve: {error}", "command": None} for error in _SEMANTIC_ERRORS],
            "diagnostics": [],
            "valid": False,
            "target": "semantic.yml",
            "resolved_path": isolated_workspace / "semantic.yml",
            "item_type": "blueprint",
        }

    def test_step_unsafe_script_path_reports_semantic_error(self, isolated_workspace: Path) -> None:
        _write_yaml(isolated_workspace / "unsafe.yml", {"id": "s", "type": "script", "script_path": "../x.sh"})

        result = _validate("unsafe.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.item_type == "step"
        assert result.errors == ["Step 's' script_path '../x.sh' is not a safe relative path."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Resolve: Step 's' script_path '../x.sh' is not a safe relative path.")
        ]

    @pytest.mark.parametrize(
        ("content", "expected_code", "expected_warnings"),
        [
            pytest.param(
                yaml.safe_dump(
                    {"name": "dup", "steps": [{"id": "s1", "type": "script", "script_path": "scripts/none.sh"}]}
                ),
                None,
                ("Step 's1' script_path 'scripts/none.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND).",),
                id="valid",
            ),
            pytest.param(
                yaml.safe_dump(_semantic_errors_and_warnings_blueprint()),
                ErrorCode.CATALOG_SCHEMA_INVALID,
                _SEMANTIC_WARNINGS,
                id="semantic",
            ),
            pytest.param("foo: bar: baz\n", ErrorCode.CATALOG_SYNTAX_INVALID, (), id="syntax"),
        ],
    )
    def test_duplicate_name_warning_comes_before_item_warnings(
        self,
        isolated_workspace: Path,
        content: str,
        expected_code: ErrorCode | None,
        expected_warnings: tuple[str, ...],
    ) -> None:
        """For 'dup' in both blueprints/dup.yml and dup.yaml, list the duplicate-name warning first, then the item's own warnings, whether it passes or fails."""
        initialized_workspace_paths(isolated_workspace)
        blueprints = isolated_workspace / ".dovo" / "catalog" / "blueprints"
        blueprints.mkdir(parents=True, exist_ok=True)
        for suffix in ("yml", "yaml"):
            (blueprints / f"dup.{suffix}").write_text(content, encoding="utf-8")

        result = _validate("dup", path=isolated_workspace)

        assert result.error_code == expected_code
        assert result.resolved_path == blueprints / "dup.yaml"
        assert result.warnings == [
            "Duplicate catalog name 'dup' at tier 'repo'; using 'blueprints/dup.yaml' "
            "(also found in: blueprints/dup.yml).",
            *expected_warnings,
        ]


class CatalogValidateAskTests:
    """Tier 1 integration tests for Catalog.validate over `type: ask` steps."""

    def test_ask_rule_violation_reports_schema_invalid_with_step_id(self, isolated_workspace: Path) -> None:
        target_path = _write_yaml(
            isolated_workspace / "ask.yml", {"name": "ask", "steps": [{"id": "approve", "type": "ask"}]}
        )

        result = _validate(str(target_path), path=isolated_workspace, item_type=CatalogItemType.BLUEPRINT)

        assert (result.error_code, result.ok) == (ErrorCode.CATALOG_SCHEMA_INVALID, False)
        assert "Step 'approve'" in result.errors[0]

    @pytest.mark.parametrize(
        ("step", "error"),
        [
            pytest.param(
                {"id": "approve", "type": "ask"},
                "steps.0: Value error, Step 'approve': 'type: ask' requires 'interaction'.",
                id="ask_without_interaction",
            ),
            pytest.param(
                {"id": "fix", "type": "agent"},
                "steps.0: Value error, Agent steps must specify a non-empty 'prompt' string.",
                id="agent_without_prompt",
            ),
            pytest.param(
                {"id": "phases", "type": "loop", "do": [{"id": "check", "run": "true"}]},
                "steps.0.until: Field required.",
                id="loop_without_until",
            ),
        ],
    )
    def test_step_schema_error_lists_only_the_declared_step_kind(
        self, isolated_workspace: Path, step: dict[str, object], error: str
    ) -> None:
        _write_yaml(isolated_workspace / "bp.yml", {"name": "bp", "steps": [step]})

        result = _validate("bp.yml", path=isolated_workspace)

        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.errors == [error]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Fix the reported schema error for target 'bp'.")]

    def test_ask_step_conflict_reports_one_schema_error(self, isolated_workspace: Path) -> None:
        _write_yaml(isolated_workspace / "ask_step.yml", {"id": "approve", "type": "ask", "run": "x"})

        result = _validate("ask_step.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.item_type == "step"
        assert result.errors == ["<root>: Value error, Step 'approve': 'type: ask' cannot be combined with run."]
        assert result.remediations == [Remediation(message="Fix the reported schema error for target 'ask_step'.")]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_BLUEPRINTS = sorted(
    [
        *(_REPO_ROOT / ".dovo" / "catalog" / "blueprints").rglob("*.yml"),
        *(_REPO_ROOT / "src" / "dovo" / "core" / "catalog" / "templates" / "blueprints").rglob("*.yml"),
    ]
)


class CatalogValidateLoopBlockTests:
    """Loop blocks reject unknown keys, and derived `do` step ids take part in the duplicate-id check."""

    def test_loop_with_unknown_keys_fails_naming_the_loop_and_sorted_keys(self, isolated_workspace: Path) -> None:
        loop = {
            "id": "phases",
            "type": "loop",
            "until": ["steps.check.exit_code == 0"],
            "untill": ["steps.check.exit_code == 0"],
            "for_each": {"artifact": "plan", "file": "plan.json", "items_path": "phases"},
            "do": [{"id": "check", "run": "true"}],
        }
        _write_yaml(isolated_workspace / "loop.yml", {"name": "loop", "steps": [loop]})

        result = _validate("loop.yml", path=isolated_workspace)

        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.errors == ["steps.0: Value error, Loop 'phases' has unknown keys: for_each, untill."]

    def test_derived_do_id_colliding_with_a_step_id_reports_duplicate(self, isolated_workspace: Path) -> None:
        steps = [
            {
                "id": "fix",
                "type": "loop",
                "until": ["steps.check.exit_code == 0"],
                "do": [{"id": "check", "run": "a"}, {"run": "b"}],
            },
            {"id": "fix-step-2", "run": "c"},
        ]
        _write_yaml(isolated_workspace / "dup.yml", {"name": "dup", "steps": steps})

        result = _validate("dup.yml", path=isolated_workspace)

        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.errors == ["Step id 'fix-step-2' is used by more than one step."]

    @pytest.mark.parametrize(
        "blueprint", [pytest.param(path, id=path.relative_to(_REPO_ROOT).as_posix()) for path in _SHIPPED_BLUEPRINTS]
    )
    def test_shipped_blueprint_validates(self, isolated_workspace: Path, blueprint: Path) -> None:
        result = _validate(str(blueprint), path=isolated_workspace)

        assert (result.error_code, result.errors) == (None, [])


class CatalogValidateWhenTests:
    """`when` scope errors are listed one per entry with their own remediation; a step catalog file rejects `when`."""

    def test_each_scope_error_is_its_own_entry_with_a_resolve_remediation(self, isolated_workspace: Path) -> None:
        steps = [
            {"id": "a", "run": "x", "when": ["steps.later.exit_code == 0"]},
            {"id": "b", "run": "y", "when": ["iteration.index > 1"]},
            {"id": "later", "run": "z"},
        ]
        _write_yaml(isolated_workspace / "when.yml", {"name": "when", "steps": steps})

        result = _validate("when.yml", path=isolated_workspace)

        expected = [
            "Step 'a': Step id 'later' referenced in when condition 'steps.later.exit_code == 0' "
            "not found in earlier steps (allowed: none).",
            "Step 'b': Operand 'iteration.index' in when condition 'iteration.index > 1' "
            "is only available inside a loop's 'do'.",
        ]
        assert result.error_code == ErrorCode.CATALOG_SCHEMA_INVALID
        assert result.errors == expected
        assert result.remediations == [Remediation(message=f"Resolve: {error}") for error in expected]

    def test_step_catalog_file_with_when_fails_validation(self, isolated_workspace: Path) -> None:
        _write_yaml(isolated_workspace / "guarded.yml", {"id": "s", "run": "x", "when": ["steps.a.exit_code == 0"]})

        result = _validate("guarded.yml", path=isolated_workspace, item_type=CatalogItemType.STEP)

        message = "Step 's' sets 'when' in a step catalog file; set 'when' on the blueprint step that uses it instead."
        assert (result.error_code, result.errors) == (ErrorCode.CATALOG_SCHEMA_INVALID, [message])
        assert result.remediations == [Remediation(message=f"Resolve: {message}")]


class _ExplodingBlueprintDefinition(BlueprintDefinition):
    """BlueprintDefinition whose document loading fails with an unexpected RuntimeError."""

    @classmethod
    def from_document(cls, raw: dict[str, object], *, key: str) -> BlueprintDefinition:
        raise RuntimeError("boom")


class CatalogValidateDiagnosticLogTests:
    """Tests verifying an unexpected schema-check failure is recorded and named in the validate result."""

    def test_unexpected_schema_failure_is_internal_error_under_catalog_validate(
        self, workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return valid False, INTERNAL_ERROR, the unexpected-error message alone and its record in diagnostics, with one catalog.validate event naming the target."""
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
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during catalog.validate: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert event["level"] == "error"
        assert event["operation"] == "catalog.validate"
        assert event["context"] == {"target": str(target_path)}
        assert event["exception_type"] == "RuntimeError"
