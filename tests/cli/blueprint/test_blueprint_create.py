"""CLI integration tests for dovo blueprint create."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths
from tests.harness.diagnostic_log import FROZEN_EVENT_ID

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

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_blueprint_create_cli_unexpected_failure_shows_hint_and_logs_event(
        self, cli_runner: CliRunner, isolated_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Exit 1, print 'Details logged:' and exc_3f9a1c07b2de, and write an event file whose JSON has every field with command "blueprint" and operation catalog.create."""
        blueprints_dir = isolated_workspace / ".dovo" / "catalog" / "blueprints"
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        blueprints_dir.chmod(0o555)
        try:
            result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "demo"])
        finally:
            blueprints_dir.chmod(0o755)

        assert result.exit_code == 1
        assert "Details logged:" in result.stdout
        assert FROZEN_EVENT_ID in result.stdout
        event = json.loads(frozen_diagnostic_clock.read_text(encoding="utf-8"))
        assert set(event) == {
            "event_id",
            "timestamp",
            "level",
            "operation",
            "command",
            "context",
            "exception_type",
            "exception_message",
            "formatted_traceback",
        }
        assert event["command"] == "blueprint"
        assert event["operation"] == "catalog.create"
        assert event["level"] == "error"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_blueprint_create_cli_unexpected_failure_json_carries_hint_in_one_envelope(
        self, cli_runner: CliRunner, isolated_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Exit 1 and print one JSON envelope whose payload errors[0] ends with the literal hint line."""
        blueprints_dir = isolated_workspace / ".dovo" / "catalog" / "blueprints"
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        blueprints_dir.chmod(0o555)
        try:
            result = cli_runner.invoke(
                app, ["-p", str(isolated_workspace), "blueprint", "create", "--name", "demo", "--format", "json"]
            )
        finally:
            blueprints_dir.chmod(0o755)

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogCreateResult"
        assert payload["payload"]["errors"][0].endswith(
            f"\nDetails logged: {frozen_diagnostic_clock} (event {FROZEN_EVENT_ID})"
        )
