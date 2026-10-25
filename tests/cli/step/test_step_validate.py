"""CLI integration tests for dovo step validate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")


def _write_repo_step(workspace: Path, name: str, definition: Mapping[str, object]) -> None:
    """Write definition as YAML to the workspace's REPO-tier steps/<name>.yml."""
    steps_dir = initialized_workspace_paths(workspace).catalog_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    (steps_dir / f"{name}.yml").write_text(yaml.safe_dump(definition), encoding="utf-8")


class StepValidateCliIntegrationTests:
    """Typer runner integration tests for dovo step validate."""

    def test_step_validate_cli_valid_step_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "valid-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "valid-step"])

        assert result.exit_code == 0
        assert "PASSED" in result.stdout

    def test_step_validate_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "json-valid-step")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "validate", "json-valid-step", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogValidateResult"
        assert payload["payload"]["valid"] is True

    def test_step_validate_cli_invalid_step_schema_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        _write_repo_step(isolated_workspace, "invalid-step", {"id": "invalid-step", "type": "unknown_type"})

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "invalid-step"])

        assert result.exit_code == 1
        assert "FAILED" in result.stdout

    def test_step_validate_cli_missing_exits_two(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "missing-step"])

        assert result.exit_code == 2
        assert "Catalog item 'missing-step' not found." in result.stdout

    def test_validate_missing_json_has_catalog_not_found(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "validate", "missing-step", "--format", "json"]
        )

        assert result.exit_code == 2
        payload = json.loads(result.stdout)["payload"]
        assert payload["status"] == "not_found"
        assert payload["errors"] == ["Catalog item 'missing-step' not found."]


class StepValidateAskCliIntegrationTests:
    """Typer runner integration tests for dovo step validate over ask steps."""

    def test_unsafe_options_from_path_exits_one_with_ask_invalid_code(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        definition = {
            "id": "pick",
            "type": "ask",
            "interaction": {
                "mode": "select",
                "message": "Pick one",
                "options_from": {
                    "artifact": "review",
                    "file": "../x.json",
                    "items_path": "findings",
                    "value_field": "id",
                    "label_field": "summary",
                },
            },
        }
        _write_repo_step(isolated_workspace, "pick", definition)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "pick", "--format", "json"])

        assert result.exit_code == 1
        errors = json.loads(result.stdout)["payload"]["errors"]
        assert errors[0].endswith("(CATALOG_ASK_INVALID).")
