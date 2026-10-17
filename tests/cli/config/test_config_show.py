"""Dual-tier matrix tests for dovo config show."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.cli.config.commands.config_show import config_show_command
from dovo.cli.context import CliContext
from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.config.models import ConfigTier, DovoConfig
from dovo.core.db.db import DovoDb
from tests.harness.workspace_paths import initialized_workspace_paths


class ConfigShowRootTests:
    """Direct handler unit tests for config_show_command."""

    def test_config_show_returns_effective_config(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        paths = initialized_workspace_paths(isolated_workspace)
        context = CliContext(paths=paths, db=DovoDb(database_file=paths.database_file, project_id=paths.project_id))
        result = config_show_command(context)

        assert result.status == ConfigLoadStatus.OK
        assert result.config_path == config_path
        assert result.config == DovoConfig.model_validate(payload)
        assert result.raw == payload
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

    def test_config_show_missing_config_returns_not_found(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        paths = initialized_workspace_paths(isolated_workspace)
        context = CliContext(paths=paths, db=DovoDb(database_file=paths.database_file, project_id=paths.project_id))
        result = config_show_command(context)

        assert result.status == ConfigLoadStatus.NOT_FOUND
        assert result.config_path == config_path
        assert result.config is None
        assert result.raw is None
        assert result.errors == [f"Configuration file not found at '{config_path}' (CONFIG_NOT_FOUND)."]
        assert result.fixes == ["Run `dovo init` to create `.dovo/config.json`"]
        assert result.warnings == []


class ConfigShowCliIntegrationTests:
    """Typer runner integration tests for dovo config show."""

    def test_config_show_cli_renders_effective_config_terminal(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 0
        assert "Config: " in res.stdout
        assert "Status: valid" in res.stdout
        assert '"version": 1' in res.stdout

    def test_config_show_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])

        assert res.exit_code == 0
        raw_payload = json.loads(config_path.read_text())
        assert json.loads(res.stdout) == {
            "event_type": "ConfigLoadResult",
            "payload": {
                "status": "ok",
                "config_path": str(config_path),
                "raw": raw_payload,
                "config": raw_payload,
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_config_show_cli_missing_config_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "CONFIG_NOT_FOUND" in res.stdout

    def test_config_show_cli_json_reflects_user_tier_override(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        write_tier_config(ConfigTier.USER, {"agents": {"copilot": {"model": "user-tier-model"}}})

        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, {"version": 1, "project": {"name": "demo-workspace"}})

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])

        assert res.exit_code == 0
        payload = json.loads(res.stdout)
        assert payload["payload"]["config"]["agents"]["copilot"]["model"] == "user-tier-model"

    def test_config_show_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        write_tier_config(ConfigTier.USER, "{not valid json")

        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "Invalid configuration in user layer" in res.stdout
