"""CLI integration tests for dovo blueprint delete."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
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

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_blueprint_delete_cli_unexpected_failure_shows_hint(
        self, cli_runner: CliRunner, isolated_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Exit 1 and print 'Details logged:' and exc_3f9a1c07b2de when the blueprints dir is read-only."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "demo")
        blueprints_dir = isolated_workspace / ".dovo" / "catalog" / "blueprints"
        blueprints_dir.chmod(0o555)
        try:
            result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "delete", "demo", "--force"])
        finally:
            blueprints_dir.chmod(0o755)

        assert result.exit_code == 1
        assert "Details logged:" in result.stdout
        assert FROZEN_EVENT_ID in result.stdout
        assert frozen_diagnostic_clock.is_file()
