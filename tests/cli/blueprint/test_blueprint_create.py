"""CLI integration tests for dovo blueprint create."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
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

    def test_blueprint_create_cli_lock_timeout_propagates(
        self, cli_runner: CliRunner, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with result.exception a LockTimeoutError and no 'Catalog Creation Failed' in stdout."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "demo"])

        assert result.exit_code == 1
        assert isinstance(result.exception, LockTimeoutError)
        assert "Catalog Creation Failed" not in result.stdout


class BlueprintCreateCliTests:
    """Typer runner tests for dovo blueprint create's usage error and classified failures."""

    def test_user_and_global_together_is_usage_error(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 2 with '--user and --global are mutually exclusive.' in output and no both-blueprint.yml in any tier."""
        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "create", "--name", "both-blueprint", "--user", "--global"],
        )

        assert result.exit_code == 2
        assert "--user and --global are mutually exclusive." in result.output
        global_paths = resolve_global_paths(None)
        assert not (isolated_workspace / ".dovo" / "catalog" / "blueprints" / "both-blueprint.yml").exists()
        assert not (global_paths.user_catalog_dir / "blueprints" / "both-blueprint.yml").exists()
        assert not (global_paths.global_catalog_dir / "blueprints" / "both-blueprint.yml").exists()

    def test_collision_json_has_already_exists_code(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 1 with error_code CATALOG_ALREADY_EXISTS."""
        cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "dup-blueprint"])

        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "create", "--name", "dup-blueprint", "--format", "json"],
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "CATALOG_ALREADY_EXISTS"

    def test_unexpected_error_reports_internal_error_json(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With scan_and_index_tier raising TypeError("bad"), exit 1 with INTERNAL_ERROR, the hint, and a catalog.create event with a traceback."""

        def _explode(*args: object) -> None:
            raise TypeError("bad")

        monkeypatch.setattr("dovo.core.catalog.services.inventory.scan_and_index_tier", _explode)

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "demo", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["errors"][0].endswith(diagnostic_hint(frozen_diagnostic_clock))
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "catalog.create"
        assert "TypeError: bad" in str(event["formatted_traceback"])
