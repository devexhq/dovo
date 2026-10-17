"""CLI integration tests for dovo blueprint list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")


class BlueprintListCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint list."""

    def test_blueprint_list_cli_renders_terminal_table(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "listed-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list"])

        assert result.exit_code == 0
        assert "listed-blueprint" in result.stdout
        assert "repo" in result.stdout
        assert "Blueprints:" in result.stdout

    def test_blueprint_list_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "json-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list", "--format", "json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogListResult"
        assert payload["payload"]["items"][0]["tier"] == "repo"

    def test_blueprint_list_cli_only_returns_blueprint_items(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exclude steps from the blueprint listing; the packaged default.yml blueprint still appears per tier."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "only-blueprint")
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.STEP, "only-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list", "--format", "json"])

        payload = json.loads(result.stdout)
        names = [item["name"] for item in payload["payload"]["items"]]
        assert names == ["only-blueprint", "default"]
        assert all(item["item_type"] == "blueprint" for item in payload["payload"]["items"])

    def test_blueprint_list_cli_uninitialized_git_repo_exits_nonzero_without_writing(
        self, cli_runner: CliRunner, git_repo: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(git_repo), "blueprint", "list"])

        assert result.exit_code != 0
        assert not (git_repo / ".dovo").exists()

    def test_blueprint_ls_alias_cli_matches_list_output(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "alias-blueprint")

        list_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "list"])
        ls_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "ls"])

        assert list_result.exit_code == 0
        assert ls_result.exit_code == 0
        assert list_result.stdout == ls_result.stdout
