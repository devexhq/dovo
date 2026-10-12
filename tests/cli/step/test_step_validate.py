"""CLI integration tests for dovo step validate."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


class StepValidateCliIntegrationTests:
    """Typer runner integration tests for dovo step validate."""

    def test_step_validate_cli_valid_step_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step validate <name>: a freshly scaffolded step passes validation; exit 0."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "valid-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "valid-step"])

        assert result.exit_code == 0
        assert "PASSED" in result.stdout

    def test_step_validate_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step validate <name> --format json: envelope reports valid=true."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "json-valid-step")

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
        """dovo step validate <name>: an invalid step fails schema validation; exit 1."""
        Catalog(_paths_for(isolated_workspace)).save(
            "invalid-step", {"id": "invalid-step", "type": "unknown_type"}, item_type=CatalogItemType.STEP
        )

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "invalid-step"])

        assert result.exit_code == 1
        assert "FAILED" in result.stdout

    def test_step_validate_cli_missing_exits_two(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step validate missing-step: exits 2 (CATALOG_ITEM_NOT_FOUND)."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "missing-step"])

        assert result.exit_code == 2
        assert "not found" in result.stdout


class StepValidateAskCliIntegrationTests:
    """Typer runner integration tests for dovo step validate over ask steps."""

    def test_unsafe_options_from_path_exits_one_with_ask_invalid_code(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo step validate --format json: a step with `options_from.file: ../x.json` exits 1 and the errors contain "(CATALOG_ASK_INVALID)."."""
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
        Catalog(_paths_for(isolated_workspace)).save("pick", definition, item_type=CatalogItemType.STEP)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "validate", "pick", "--format", "json"])

        assert result.exit_code == 1
        errors = json.loads(result.stdout)["payload"]["errors"]
        assert errors[0].endswith("(CATALOG_ASK_INVALID).")
