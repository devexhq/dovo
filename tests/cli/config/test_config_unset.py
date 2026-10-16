"""CLI integration tests for dovo config unset."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config


class ConfigUnsetCliIntegrationTests:
    """Typer runner integration tests for dovo config unset."""

    def test_config_unset_cli_removes_value_and_exits_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agents"] = {"copilot": {"model": "gpt-4.1"}}
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "unset", "agents.copilot.model"],
        )

        assert res.exit_code == 0
        assert "Config unset: agents.copilot.model" in res.stdout
        assert json.loads(config_path.read_text())["agents"] == {"copilot": {}}

    def test_config_unset_cli_missing_key_exits_zero_without_write(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "unset", "telemetry.nonexistent"],
        )

        assert res.exit_code == 0
        assert config_path.read_bytes() == before

    def test_config_unset_cli_schema_violation_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "unset", "project"],
        )

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "CONFIG_SCHEMA_INVALID" in res.stdout

    def test_config_unset_cli_format_json_emits_event(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agents"] = {"copilot": {"model": "gpt-4.1"}}
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "unset", "agents.copilot.model", "--format", "json"],
        )

        assert res.exit_code == 0
        assert json.loads(res.stdout) == {
            "event_type": "ConfigUnsetResult",
            "payload": {
                "status": "ok",
                "config_path": str(config_path),
                "key": "agents.copilot.model",
                "existed": True,
                "previous_value": "gpt-4.1",
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
