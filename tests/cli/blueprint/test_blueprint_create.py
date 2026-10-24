"""CLI integration tests for dovo blueprint create."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from tests.harness.lock import raise_lock_timeout

pytestmark = pytest.mark.usefixtures("workspace_config")


class BlueprintCreateCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint create."""

    def test_blueprint_create_cli_creates_item_terminal(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "new-blueprint"]
        )

        assert result.exit_code == 0
        assert "Created blueprint 'new-blueprint' (tier: repo)" in result.stdout
        target_file = isolated_workspace / ".dovo" / "catalog" / "blueprints" / "new-blueprint.yml"
        assert target_file.is_file()

    def test_blueprint_create_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "create", "--name", "json-blueprint", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogCreateResult"
        assert payload["payload"]["status"] == "ok"
        assert payload["payload"]["item"]["tier"] == "repo"
        assert payload["payload"]["item"]["item_type"] == "blueprint"

    def test_blueprint_create_cli_collision_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "dup-blueprint"])

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "dup-blueprint"]
        )

        assert result.exit_code == 1
        assert "collision" in result.stdout

    @pytest.mark.parametrize(
        ("tier", "catalog_dir_attr"),
        [
            pytest.param("user", "user_catalog_dir", id="user"),
            pytest.param("global", "global_catalog_dir", id="global"),
        ],
    )
    def test_blueprint_create_cli_tier_flag_writes_tier_file(
        self, cli_runner: CliRunner, isolated_workspace: Path, tier: str, catalog_dir_attr: str
    ) -> None:
        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "blueprint",
                "create",
                "--name",
                "tier-blueprint",
                f"--{tier}",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["item"]["tier"] == tier
        catalog_dir: Path = getattr(resolve_global_paths(None), catalog_dir_attr)
        assert (catalog_dir / "blueprints" / "tier-blueprint.yml").is_file()

    def test_blueprint_create_cli_user_and_global_together_exits_one(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "create", "--name", "both-blueprint", "--user", "--global"],
        )

        assert result.exit_code == 1
        assert "mutually exclusive" in result.stdout
        global_paths = resolve_global_paths(None)
        assert not (global_paths.user_catalog_dir / "blueprints" / "both-blueprint.yml").exists()
        assert not (global_paths.global_catalog_dir / "blueprints" / "both-blueprint.yml").exists()

    def test_blueprint_create_cli_lock_timeout_propagates(
        self, cli_runner: CliRunner, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with result.exception a LockTimeoutError and no 'Catalog Creation Failed' in stdout."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "demo"])

        assert result.exit_code == 1
        assert isinstance(result.exception, LockTimeoutError)
        assert "Catalog Creation Failed" not in result.stdout
