"""CLI integration tests for dovo blueprint validate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.catalog.models import CatalogItemType
from tests.harness.ask import approval_gate_blueprint, review_triage_blueprint, revise_loop_blueprint
from tests.harness.diagnostic_log import FROZEN_EVENT_ID, read_single_event
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")

_SEMANTIC_BLUEPRINT: dict[str, object] = {
    "name": "w",
    "inputs": {"foo": {"type": "string", "aliases": ["-f"]}, "bar": {"type": "string", "aliases": ["-f"]}},
    "steps": [
        {"id": "a", "run": "echo ${{ inputs.ghost }}"},
        {"id": "a", "type": "script", "script_path": "nope.sh"},
    ],
}
_SEMANTIC_ERRORS: tuple[str, ...] = (
    "Step id 'a' is used by more than one step.",
    "Alias '-f' is declared by both 'foo' and 'bar'.",
)
_SEMANTIC_WARNINGS: tuple[str, ...] = (
    "Step 'a' script_path 'nope.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND).",
    "Placeholder 'inputs.ghost' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER).",
)


class BlueprintValidateCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint validate."""

    def test_blueprint_validate_cli_valid_blueprint_exits_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "valid-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", "valid-blueprint"])

        assert result.exit_code == 0
        assert "PASSED" in result.stdout

    def test_blueprint_validate_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(
            CatalogItemType.BLUEPRINT, "json-valid-blueprint"
        )

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "validate", "json-valid-blueprint", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogValidateResult"
        assert payload["payload"]["valid"] is True

    def test_blueprint_validate_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", "missing-blueprint"])

        assert result.exit_code == 1
        assert "Catalog item 'missing-blueprint' not found." in result.stdout

    def test_validate_missing_json_has_catalog_not_found(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "validate", "missing-blueprint", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "CATALOG_NOT_FOUND"
        assert payload["errors"] == ["Catalog item 'missing-blueprint' not found."]


class BlueprintValidateAskCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint validate over ask blueprints."""

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param(approval_gate_blueprint(), id="approval-gate"),
            pytest.param(revise_loop_blueprint(), id="revise-loop"),
            pytest.param(review_triage_blueprint(), id="review-triage"),
        ],
    )
    def test_worked_ask_blueprints_validate_with_exit_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path, document: dict[str, object]
    ) -> None:
        target = isolated_workspace / "worked.yml"
        target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", str(target)])

        assert result.exit_code == 0, result.stdout


def _issue_1084_loop(**fields: object) -> dict[str, object]:
    """Return the loop block of the GH-1084 steps to reproduce, with fields overriding its keys."""
    loop: dict[str, object] = {
        "id": "phases",
        "type": "loop",
        "until": ["steps.check.exit_code == 0"],
        "for_each": {"artifact": "plan", "file": "plan.json", "items_path": "phases"},
        "do": [{"id": "check", "run": "true"}],
    }
    return {**loop, **fields}


class BlueprintValidateLoopCliIntegrationTests:
    """Typer runner regression tests for GH-1084: loop blocks reject unknown keys and `do` steps get the shorthands."""

    def _validate(
        self, cli_runner: CliRunner, workspace: Path, loop: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        target = workspace / "phases.yml"
        target.write_text(yaml.safe_dump({"name": "phases", "steps": [loop]}, sort_keys=False), encoding="utf-8")
        result = cli_runner.invoke(
            app, ["-p", str(workspace), "blueprint", "validate", str(target), "--format", "json"]
        )
        return result.exit_code, json.loads(result.stdout)["payload"]

    def test_issue_1084_loop_with_unknown_key_fails(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        exit_code, payload = self._validate(cli_runner, isolated_workspace, _issue_1084_loop())

        assert exit_code == 1
        assert payload["error_code"] == "CATALOG_SCHEMA_INVALID"
        assert payload["errors"] == ["steps.0: Value error, Loop 'phases' has unknown keys: for_each."]

    @pytest.mark.parametrize(
        "do",
        [
            pytest.param([{"name": "Run tests", "run": "uv run pytest"}, {"run": "echo fix"}], id="steps_without_id"),
            pytest.param([{"id": "check", "command": "echo hi"}], id="command_shorthand"),
        ],
    )
    def test_issue_1084_do_steps_take_the_shorthands(
        self, cli_runner: CliRunner, isolated_workspace: Path, do: list[dict[str, object]]
    ) -> None:
        loop = _issue_1084_loop(until=["iteration.index == 1"], do=do)
        del loop["for_each"]

        exit_code, payload = self._validate(cli_runner, isolated_workspace, loop)

        assert (exit_code, payload["errors"]) == (0, [])


class BlueprintValidateCliTests:
    """Typer runner tests for dovo blueprint validate's classified and unexpected failures."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_unreadable_file_json_has_file_unreadable(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 1 with error_code CATALOG_FILE_UNREADABLE and valid False for a mode-000 file target."""
        target = isolated_workspace / "noperm.yml"
        target.write_text(yaml.safe_dump({"name": "noperm", "steps": [{"id": "s1", "run": "echo hi"}]}), "utf-8")
        target.chmod(0o000)
        try:
            result = cli_runner.invoke(
                app, ["-p", str(isolated_workspace), "blueprint", "validate", str(target), "--format", "json"]
            )
        finally:
            target.chmod(0o644)

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "CATALOG_FILE_UNREADABLE"
        assert payload["valid"] is False

    def test_invalid_definition_json_has_schema_invalid(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 1 with error_code CATALOG_SCHEMA_INVALID, valid False and payload keys exactly {valid, status_label, target, resolved_path, item_type, error_code, errors, warnings, remediations, diagnostics} for a file target failing schema validation."""
        target = isolated_workspace / "bad.yml"
        target.write_text(yaml.safe_dump({"name": "bad", "timeout_seconds": "not-a-number", "steps": []}), "utf-8")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "validate", str(target), "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "CATALOG_SCHEMA_INVALID"
        assert payload["valid"] is False
        assert set(payload) == {
            "valid",
            "status_label",
            "target",
            "resolved_path",
            "item_type",
            "error_code",
            "errors",
            "warnings",
            "remediations",
            "diagnostics",
        }

    def test_semantic_errors_and_warnings_json_payload(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 1 with the exact payload for a file target with two semantic errors and two warnings."""
        target = isolated_workspace / "semantic.yml"
        target.write_text(yaml.safe_dump(_SEMANTIC_BLUEPRINT, sort_keys=False), "utf-8")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "validate", str(target), "--format", "json"]
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["payload"] == {
            "error_code": "CATALOG_SCHEMA_INVALID",
            "errors": list(_SEMANTIC_ERRORS),
            "warnings": list(_SEMANTIC_WARNINGS),
            "remediations": [{"message": f"Resolve: {error}", "command": None} for error in _SEMANTIC_ERRORS],
            "diagnostics": [],
            "valid": False,
            "status_label": "FAILED",
            "target": str(target),
            "resolved_path": target.as_posix(),
            "item_type": "blueprint",
        }

    def test_semantic_errors_and_warnings_render_codes(self, isolated_workspace: Path) -> None:
        """Exit 1 and show FAILED, the error_code beside each error, each error's remediation and each warning's own code."""
        target = isolated_workspace / "semantic.yml"
        target.write_text(yaml.safe_dump(_SEMANTIC_BLUEPRINT, sort_keys=False), "utf-8")
        wide_runner = CliRunner(env={"NO_COLOR": "1", "COLUMNS": "400"})

        result = wide_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "validate", str(target)])

        assert result.exit_code == 1
        assert "FAILED" in result.stdout
        assert result.stdout.count("CATALOG_SCHEMA_INVALID") == len(_SEMANTIC_ERRORS)
        for error in _SEMANTIC_ERRORS:
            assert error in result.stdout
            assert f"Resolve: {error}" in result.stdout
        assert "CATALOG_SCRIPT_NOT_FOUND" in result.stdout
        assert "CATALOG_UNDECLARED_INPUT_PLACEHOLDER" in result.stdout

    def test_unexpected_definition_error_reports_internal_error_json(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With BlueprintDefinition.from_document raising RuntimeError("boom"), exit 1 with INTERNAL_ERROR, the unexpected-error message alone, the record in diagnostics, and one catalog.validate event."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "demo")

        def _explode(cls: type[BlueprintDefinition], raw: dict[str, object], *, key: str) -> BlueprintDefinition:
            raise RuntimeError("boom")

        monkeypatch.setattr(BlueprintDefinition, "from_document", classmethod(_explode))

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "validate", "demo", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["errors"] == ["Unexpected error during catalog.validate: boom"]
        assert payload["diagnostics"] == [{"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "catalog.validate"
        assert event["exception_type"] == "RuntimeError"
