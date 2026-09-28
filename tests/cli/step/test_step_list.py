"""CLI integration tests for wt step list."""

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


class StepListCliIntegrationTests:
    """Typer runner integration tests for wt step list."""

    def test_step_list_cli_renders_terminal_table(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt step list: one repo-tier step on disk renders its name/tier/sha in the terminal table; exit 0."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "listed-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list"])

        assert result.exit_code == 0
        assert "listed-step" in result.stdout
        assert "repo" in result.stdout
        assert "Steps:" in result.stdout
        assert "Blueprints:" not in result.stdout

    def test_step_list_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt step list --format json: envelope's items include a 'tier' key absent from the old wt catalog list payload."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "json-step")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list", "--format", "json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogListResult"
        assert payload["payload"]["items"][0]["tier"] == "repo"

    def test_step_list_cli_only_returns_step_items(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt step list: a blueprint created alongside a step is excluded from the step listing (the packaged default.yml step template still appears, folded into items per every tier)."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "only-step")
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "only-blueprint")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list", "--format", "json"])

        payload = json.loads(result.stdout)
        names = [item["name"] for item in payload["payload"]["items"]]
        assert names == ["only-step", "default"]
        assert all(item["item_type"] == "step" for item in payload["payload"]["items"])

    def test_step_list_cli_uninitialized_git_repo_exits_nonzero_without_writing(
        self, cli_runner: CliRunner, git_repo: Path
    ) -> None:
        """wt step list: non-worktree, non-git directory exits nonzero and writes no .worktree/ directory."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "step", "list"])

        assert result.exit_code != 0
        assert not (git_repo / ".worktree").exists()

    def test_step_ls_alias_cli_matches_list_output(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt step ls: renders byte-identical terminal output to wt step list for the same workspace."""
        Catalog(_paths_for(isolated_workspace)).create(CatalogItemType.STEP, "alias-step")

        list_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "list"])
        ls_result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "ls"])

        assert list_result.exit_code == 0
        assert ls_result.exit_code == 0
        assert list_result.stdout == ls_result.stdout
