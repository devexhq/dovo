"""CLI integration tests for dovo step show."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from dovo.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class StepShowCliIntegrationTests:
    """Typer runner integration tests for dovo step show."""

    def test_step_show_cli_renders_terminal_metadata(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step show <name>: renders step metadata and YAML definition; exit 0."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "show-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "show", "show-step"])

        assert result.exit_code == 0
        assert "show-step" in result.stdout
        assert "Step:" in result.stdout
        assert "Blueprint:" not in result.stdout

    def test_step_show_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step show <name> --format json: envelope's item is tagged tier='repo'."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "json-show-step")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "show", "json-show-step", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogShowResult"
        assert payload["payload"]["item"]["tier"] == "repo"

    def test_step_show_cli_scopes_to_step_type(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step show <name>: a same-named blueprint is not returned; exits 1 not found."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "shared-name")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "show", "shared-name"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_step_show_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step show missing-step: exits 1 with a not-found message."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "show", "missing-step"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
