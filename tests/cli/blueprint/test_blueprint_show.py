"""CLI integration tests for wt blueprint show."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.catalog import Catalog
from worktree.core.catalog.models import CatalogItemType
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class BlueprintShowCliIntegrationTests:
    """Typer runner integration tests for wt blueprint show."""

    def test_blueprint_show_cli_renders_terminal_metadata(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """wt blueprint show <name>: renders blueprint metadata and YAML definition; exit 0."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "show-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "show", "show-blueprint"])

        assert result.exit_code == 0
        assert "show-blueprint" in result.stdout
        assert "Blueprint:" in result.stdout

    def test_blueprint_show_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt blueprint show <name> --format json: envelope's item is tagged tier='repo'."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "json-show-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "show", "json-show-blueprint", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogShowResult"
        assert payload["payload"]["item"]["tier"] == "repo"

    def test_blueprint_show_cli_scopes_to_blueprint_type(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt blueprint show <name>: a same-named step is not returned; exits 1 not found."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "shared-name")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "show", "shared-name"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_blueprint_show_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt blueprint show missing-blueprint: exits 1 with a not-found message."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "show", "missing-blueprint"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
