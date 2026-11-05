"""Typer runner tests for dovo config show."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config
from dovo.core.config.models import ConfigTier
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result

WriteTierConfig = Callable[[ConfigTier, dict[str, Any] | str], Path]


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
            "version": 1,
            "event_type": "config.show",
            "payload": {
                "ok": True,
                "error_code": None,
                "errors": [],
                "warnings": [],
                "remediations": [],
                "diagnostics": [],
                "config_path": str(config_path),
                "raw": raw_payload,
                "config": raw_payload,
            },
        }

    def test_config_show_cli_missing_config_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "Configuration file not found" in res.stdout

    def test_config_show_cli_missing_config_json_reports_config_not_found(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 1 with error_code CONFIG_NOT_FOUND and no status key."""
        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])

        assert res.exit_code == 1
        payload = json.loads(res.stdout)["payload"]
        assert "status" not in payload
        assert payload["error_code"] == "CONFIG_NOT_FOUND"

    def test_config_show_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        write_tier_config: WriteTierConfig,
    ) -> None:
        write_tier_config(ConfigTier.USER, "{not valid json")

        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "Invalid configuration in user tier" in res.stdout

    def test_config_show_cli_malformed_user_tier_json_reports_tier_code(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        write_tier_config: WriteTierConfig,
    ) -> None:
        """Exit 1 with error_code CONFIG_TIER_JSON_INVALID and the tier-attributed message."""
        tier_config_path = write_tier_config(ConfigTier.USER, "")
        Filesystem.atomic_write_json(
            isolated_workspace / ".dovo" / "config.json", build_default_config("demo-workspace")
        )

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])

        assert res.exit_code == 1
        payload = json.loads(res.stdout)["payload"]
        assert payload["error_code"] == "CONFIG_TIER_JSON_INVALID"
        assert payload["errors"] == [
            f"Invalid configuration in user tier ({tier_config_path}): Expecting value at line 1 column 1 (char 0)"
        ]
        assert payload["diagnostics"] == []

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("config_text", "exit_code", "error_code"),
        [
            pytest.param(json.dumps(build_default_config("demo-workspace")), 0, None, id="valid_config"),
            pytest.param("{not valid json", 1, "CONFIG_JSON_INVALID", id="malformed_config"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        output_format: str,
        config_text: str,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        (isolated_workspace / ".dovo" / "config.json").write_text(config_text, encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", output_format])

        assert_exit_follows_result(result, output_format, exit_code, error_code)
