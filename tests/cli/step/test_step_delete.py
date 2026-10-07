"""CLI integration tests for dovo step delete."""

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


class StepDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo step delete."""

    def test_step_delete_cli_with_force_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step delete <name> --force: repo-tier file removed from disk; exit 0."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "del-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "del-step", "--force"])

        assert result.exit_code == 0
        assert not (isolated_workspace / ".dovo" / "catalog" / "steps" / "del-step.yml").exists()

    def test_step_delete_cli_confirmation_declined_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo step delete <name>, answering 'n' at the prompt: cancels and exits 1."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "declined-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "declined-step"], input="n\n")

        assert result.exit_code == 1
        assert "Deletion cancelled" in result.stdout

    def test_step_delete_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step delete <name> --force --format json: envelope reports deleted=true."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "json-del-step")

        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "step", "delete", "json-del-step", "--force", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogDeleteResult"
        assert payload["payload"]["deleted"] is True

    def test_step_delete_cli_bundled_template_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step delete dovo/<name> --force: exit 1, CatalogProtectionError message in stdout, file untouched."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "dovo/lint", "--force"])

        assert result.exit_code == 1
        assert "Cannot delete bundled catalog template" in result.stdout

    def test_step_delete_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step delete missing-step --force: exits 1 with a not-found message."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "delete", "missing-step", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
