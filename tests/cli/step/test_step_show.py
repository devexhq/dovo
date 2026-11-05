"""CLI integration tests for dovo step show."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")


class StepShowCliIntegrationTests:
    """Typer runner integration tests for dovo step show."""

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("name", "exit_code", "error_code"),
        [
            pytest.param("known-step", 0, None, id="known_step"),
            pytest.param("missing-step", 1, "CATALOG_NOT_FOUND", id="unknown_step"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        output_format: str,
        name: str,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "known-step")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "show", name, "--format", output_format]
        )

        assert_exit_follows_result(result, output_format, exit_code, error_code)

    def test_step_show_cli_renders_terminal_metadata(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "show-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "show", "show-step"])

        assert result.exit_code == 0
        assert "show-step" in result.stdout
        assert "Step:" in result.stdout
        assert "Blueprint:" not in result.stdout

    def test_step_show_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "json-show-step")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "show", "json-show-step", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "step.show"
        assert payload["payload"]["item"]["tier"] == "repo"
        assert payload["payload"]["item_type"] == "step"

    def test_step_show_cli_scopes_to_step_type(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "shared-name")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "show", "shared-name"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_show_missing_json_has_catalog_not_found(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "show", "missing-step", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["item"] is None
        assert payload["item_type"] == "step"
        assert payload["error_code"] == "CATALOG_NOT_FOUND"
        assert payload["errors"] == ["Catalog item 'missing-step' not found."]
