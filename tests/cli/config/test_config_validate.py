"""Typer runner tests for dovo config validate."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config


class ConfigValidateCliIntegrationTests:
    """Typer runner integration tests for dovo config validate."""

    def test_config_validate_cli_clean_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "validate"])

        assert res.exit_code == 0
        assert "Status: valid" in res.stdout
        assert "Config is valid." in res.stdout

    def test_config_validate_cli_warnings_exits_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path, model_required_provider: None
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "validate"])

        assert res.exit_code == 0
        assert "Status: valid with warnings" in res.stdout
        assert "Warnings:" in res.stdout

    def test_config_validate_cli_schema_error_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        invalid_payload = {"version": 1}
        Filesystem.atomic_write_json(config_path, invalid_payload)

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "validate"])

        assert res.exit_code == 1
        assert "Config Validation Failed" in res.stdout
        assert "Config schema validation failed" in res.stdout

    def test_config_validate_cli_format_json_emits_event(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "validate", "--format", "json"],
        )

        assert res.exit_code == 0
        raw_payload = json.loads(config_path.read_text())
        assert json.loads(res.stdout) == {
            "event_type": "ConfigValidationResult",
            "payload": {
                "status": "valid",
                "config_path": str(config_path),
                "status_label": "valid",
                "raw": raw_payload,
                "config": raw_payload,
                "errors": [],
                "warnings": [],
                "remediations": [],
                "diagnostics": [],
            },
        }
