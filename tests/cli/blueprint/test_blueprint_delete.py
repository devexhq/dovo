"""CLI integration tests for dovo blueprint delete."""

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


class BlueprintDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint delete."""

    def test_blueprint_delete_cli_with_force_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "del-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "del-blueprint", "--force"]
        )

        assert result.exit_code == 0
        assert not (isolated_workspace / ".dovo" / "catalog" / "blueprints" / "del-blueprint.yml").exists()

    def test_blueprint_delete_cli_confirmation_declined_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "declined-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "declined-blueprint"], input="n\n"
        )

        assert result.exit_code == 1
        assert "Deletion cancelled" in result.stdout

    def test_blueprint_delete_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "json-del-blueprint")

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "blueprint",
                "delete",
                "json-del-blueprint",
                "--force",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogDeleteResult"
        assert payload["payload"]["deleted"] is True

    def test_blueprint_delete_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "missing-blueprint", "--force"]
        )

        assert result.exit_code == 1
        assert "not found" in result.stdout
