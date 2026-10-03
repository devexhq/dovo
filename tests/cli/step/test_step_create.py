"""CLI integration tests for dovo step create."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths


class StepCreateCliIntegrationTests:
    """Typer runner integration tests for dovo step create."""

    def test_step_create_cli_creates_item_terminal(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step create --name <name>: scaffolds a repo-tier file and renders confirmation; exit 0."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create", "--name", "new-step"])

        assert result.exit_code == 0
        assert "Created step 'new-step' (tier: repo)" in result.stdout
        target_file = isolated_workspace / ".dovo" / "catalog" / "steps" / "new-step.yml"
        assert target_file.is_file()

    def test_step_create_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step create --name <name> --format json: envelope's item is tagged tier='repo', type='step'."""
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "create", "--name", "json-step", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogCreateResult"
        assert payload["payload"]["item"]["tier"] == "repo"
        assert payload["payload"]["item"]["item_type"] == "step"

    def test_step_create_cli_collision_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step create --name <existing name>: exits 1 on path collision."""
        cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create", "--name", "dup-step"])

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create", "--name", "dup-step"])

        assert result.exit_code == 1
        assert "collision" in result.stdout

    def test_step_create_cli_requires_name_option(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo step create with no --name exits nonzero (missing required option)."""
        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "step", "create"])

        assert result.exit_code != 0

    def test_step_create_cli_user_flag_writes_user_tier_file(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo step create --user: writes <DOVO_HOME>/user/catalog/steps/<name>.yml, JSON payload item.tier == 'user'."""
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "step", "create", "--name", "user-step", "--user", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["payload"]["item"]["tier"] == "user"
        target_file = resolve_global_paths(None).user_catalog_dir / "steps" / "user-step.yml"
        assert target_file.is_file()

    def test_step_create_cli_global_flag_writes_global_tier_file(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo step create --global: writes <DOVO_HOME>/global/catalog/steps/<name>.yml, JSON payload item.tier == 'global'."""
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "step", "create", "--name", "global-step", "--global", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["payload"]["item"]["tier"] == "global"
        target_file = resolve_global_paths(None).global_catalog_dir / "steps" / "global-step.yml"
        assert target_file.is_file()

    def test_step_create_cli_user_and_global_together_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo step create --user --global: exit 1, 'mutually exclusive' in stdout, no file written under either tier."""
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "step", "create", "--name", "both-step", "--user", "--global"]
        )

        assert result.exit_code == 1
        assert "mutually exclusive" in result.stdout
        global_paths = resolve_global_paths(None)
        assert not (global_paths.user_catalog_dir / "steps" / "both-step.yml").exists()
        assert not (global_paths.global_catalog_dir / "steps" / "both-step.yml").exists()
