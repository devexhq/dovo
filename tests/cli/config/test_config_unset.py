"""CLI integration tests for dovo config unset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result


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
        assert "Config schema validation failed" in res.stdout

    def test_config_unset_cli_absent_key_on_schema_invalid_config_reports_show_failure(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 1 with the CONFIG_SCHEMA_INVALID failure config show reports, leaving the file unchanged."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["worktree"]["max_active_worktrees"] = "three"
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()
        shown = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "unset", "telemetry.nosuch", "--format", "json"],
        )

        assert res.exit_code == 1
        unset_payload = json.loads(res.stdout)["payload"]
        show_payload = json.loads(shown.stdout)["payload"]
        assert unset_payload["error_code"] == "CONFIG_SCHEMA_INVALID"
        assert "worktree.max_active_worktrees: 'three' is not of type 'integer'" in "\n".join(unset_payload["errors"])
        assert (unset_payload["errors"], unset_payload["remediations"], unset_payload["diagnostics"]) == (
            show_payload["errors"],
            show_payload["remediations"],
            show_payload["diagnostics"],
        )
        assert config_path.read_bytes() == before

    def test_config_unset_cli_missing_config_json_reports_config_not_found(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 1 with error_code CONFIG_NOT_FOUND and no status key."""
        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "unset", "agents.copilot.model", "--format", "json"],
        )

        assert res.exit_code == 1
        payload = json.loads(res.stdout)["payload"]
        assert "status" not in payload
        assert payload["error_code"] == "CONFIG_NOT_FOUND"

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
                "ok": True,
                "config_path": str(config_path),
                "key": "agents.copilot.model",
                "existed": True,
                "previous_value": "gpt-4.1",
                "error_code": None,
                "errors": [],
                "warnings": [],
                "remediations": [],
                "diagnostics": [],
            },
        }

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("key", "exit_code", "error_code"),
        [
            pytest.param("agents.copilot.model", 0, None, id="valid_key"),
            pytest.param("", 2, "CONFIG_KEY_PATH_INVALID", id="empty_key"),
            pytest.param("a..b", 2, "CONFIG_KEY_PATH_INVALID", id="empty_segment"),
            pytest.param("project", 1, "CONFIG_SCHEMA_INVALID", id="schema_required_key"),
        ],
    )
    @pytest.mark.usefixtures("workspace_config")
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        output_format: str,
        key: str,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "config", "unset", "--format", output_format, "--", key]
        )

        assert_exit_follows_result(result, output_format, exit_code, error_code)
