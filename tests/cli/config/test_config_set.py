"""Typer runner tests for dovo config set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config


class ConfigSetCliIntegrationTests:
    """Typer runner integration tests for dovo config set."""

    def test_config_set_cli_mutates_value_and_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "set", "agents.copilot.model", "qwen2.5-coder"],
        )

        assert res.exit_code == 0
        assert "Config updated: agents.copilot.model" in res.stdout
        assert json.loads(config_path.read_text())["agents"]["copilot"]["model"] == "qwen2.5-coder"

    def test_config_set_default_agent_claude_persists_value(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "set", "default_agent", "claude"])

        assert result.exit_code == 0
        assert "Config updated: default_agent" in result.stdout
        assert json.loads(config_path.read_text())["default_agent"] == "claude"

    def test_config_set_cli_stores_boolean_true_from_string(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "set", "telemetry.enabled", "true"],
        )

        assert res.exit_code == 0
        assert "(bool)" in res.stdout
        assert json.loads(config_path.read_text())["telemetry"]["enabled"] is True

    @pytest.mark.parametrize(
        ("key", "raw_value", "expected_line"),
        [
            pytest.param(
                "worktree.base_ref",
                '"12345"',
                "✔  Config updated: worktree.base_ref = 12345 (str)",
                id="quoted-numeric",
            ),
            pytest.param(
                "agents.copilot.model",
                '"true"',
                "✔  Config updated: agents.copilot.model = true (str)",
                id="quoted-bool",
            ),
        ],
    )
    def test_config_set_cli_keeps_quoted_token_as_string(
        self, cli_runner: CliRunner, isolated_workspace: Path, key: str, raw_value: str, expected_line: str
    ) -> None:
        """Exit 0, print expected_line and store the unquoted text as a JSON string at key."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "set", key, raw_value])

        assert res.exit_code == 0
        assert expected_line in res.stdout
        stored: object = json.loads(config_path.read_text())
        for part in key.split("."):
            assert isinstance(stored, dict)
            stored = stored[part]
        assert stored == raw_value[1:-1]

    def test_config_set_cli_schema_violation_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "config",
                "set",
                "--",
                "worktree.max_active_worktrees",
                "-1",
            ],
        )

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "CONFIG_SCHEMA_INVALID" in res.stdout

    def test_config_set_cli_format_json_emits_event(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "config",
                "set",
                "agents.copilot.model",
                "qwen2.5-coder",
                "--format",
                "json",
            ],
        )

        assert res.exit_code == 0
        assert json.loads(res.stdout) == {
            "event_type": "ConfigSetResult",
            "payload": {
                "status": "ok",
                "config_path": str(config_path),
                "key": "agents.copilot.model",
                "value": "qwen2.5-coder",
                "value_str": "qwen2.5-coder",
                "value_type": "str",
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
