"""CLI integration tests for dovo blueprint create."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths


class BlueprintCreateCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint create."""

    def test_blueprint_create_cli_creates_item_terminal(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo blueprint create --name <name>: scaffolds a repo-tier file and renders confirmation; exit 0."""
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "new-blueprint"]
        )

        assert result.exit_code == 0
        assert "Created blueprint 'new-blueprint' (tier: repo)" in result.stdout
        target_file = isolated_workspace / ".dovo" / "catalog" / "blueprints" / "new-blueprint.yml"
        assert target_file.is_file()

    def test_blueprint_create_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo blueprint create --name <name> --format json: envelope's item is tagged tier='repo', type='blueprint'."""
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "create", "--name", "json-blueprint", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogCreateResult"
        assert payload["payload"]["item"]["tier"] == "repo"
        assert payload["payload"]["item"]["item_type"] == "blueprint"

    def test_blueprint_create_cli_collision_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo blueprint create --name <existing name>: exits 1 on path collision."""
        cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "dup-blueprint"])

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "dup-blueprint"]
        )

        assert result.exit_code == 1
        assert "collision" in result.stdout

    def test_blueprint_create_cli_requires_name_option(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo blueprint create with no --name exits nonzero (missing required option)."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create"])

        assert result.exit_code != 0

    def test_blueprint_create_cli_user_flag_writes_user_tier_file(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo blueprint create --user: writes <DOVO_HOME>/user/catalog/blueprints/<name>.yml, JSON payload item.tier == 'user'."""
        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "blueprint",
                "create",
                "--name",
                "user-blueprint",
                "--user",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["payload"]["item"]["tier"] == "user"
        target_file = resolve_global_paths(None).user_catalog_dir / "blueprints" / "user-blueprint.yml"
        assert target_file.is_file()

    def test_blueprint_create_cli_global_flag_writes_global_tier_file(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo blueprint create --global: writes <DOVO_HOME>/global/catalog/blueprints/<name>.yml, JSON payload item.tier == 'global'."""
        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "blueprint",
                "create",
                "--name",
                "global-blueprint",
                "--global",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["payload"]["item"]["tier"] == "global"
        target_file = resolve_global_paths(None).global_catalog_dir / "blueprints" / "global-blueprint.yml"
        assert target_file.is_file()

    def test_blueprint_create_cli_user_and_global_together_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo blueprint create --user --global: exit 1, 'mutually exclusive' in stdout, no file written under either tier."""
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "create", "--name", "both-blueprint", "--user", "--global"],
        )

        assert result.exit_code == 1
        assert "mutually exclusive" in result.stdout
        global_paths = resolve_global_paths(None)
        assert not (global_paths.user_catalog_dir / "blueprints" / "both-blueprint.yml").exists()
        assert not (global_paths.global_catalog_dir / "blueprints" / "both-blueprint.yml").exists()
