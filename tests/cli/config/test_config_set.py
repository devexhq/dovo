"""Dual-tier matrix tests for dovo config set."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.cli.config.commands.config_set import config_set_command
from dovo.cli.context import CliContext
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.config.generator import build_default_config
from dovo.core.config.mutate import ConfigSetStatus
from dovo.core.db.db import DovoDb
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


class ConfigSetRootTests:
    """Direct handler unit tests for config_set_command."""

    def test_config_set_updates_scalar_value_returns_ok(self, isolated_workspace: Path) -> None:
        """Handler returns OK ConfigSetResult and mutates config atomically."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        paths = _paths_for(isolated_workspace)
        context = CliContext(paths=paths, db=DovoDb(database_file=paths.database_file, project_id=paths.project_id))
        result = config_set_command(context, "agent.model", "qwen2.5-coder")

        assert result.status == ConfigSetStatus.OK
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.value == "qwen2.5-coder"
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert json.loads(config_path.read_text())["agent"]["model"] == "qwen2.5-coder"

    def test_config_set_schema_violation_returns_error(self, isolated_workspace: Path) -> None:
        """Handler returns SCHEMA_INVALID ConfigSetResult on bad schema key."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        paths = _paths_for(isolated_workspace)
        context = CliContext(paths=paths, db=DovoDb(database_file=paths.database_file, project_id=paths.project_id))
        result = config_set_command(context, "worktrees.max_active_worktrees", "3")

        assert result.status == ConfigSetStatus.SCHEMA_INVALID
        assert result.config_path == config_path
        assert result.key == "worktrees.max_active_worktrees"
        assert result.value == 3
        assert result.errors == [
            "Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- (root): Additional properties are not allowed ('worktrees' was unexpected)"
        ]
        assert result.fixes == [
            "Run `dovo config validate` for details",
            "Or `dovo init --repair` to insert missing keys without overwriting values",
        ]
        assert result.warnings == []
        assert json.loads(config_path.read_text()) == payload


class ConfigSetCliIntegrationTests:
    """Typer runner integration tests for dovo config set."""

    def test_config_set_cli_mutates_value_and_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo config set updates value and displays confirmation."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "config", "set", "agent.model", "qwen2.5-coder"],
        )

        assert res.exit_code == 0
        assert "Config updated: agent.model" in res.stdout
        assert json.loads(config_path.read_text())["agent"]["model"] == "qwen2.5-coder"

    def test_config_set_cli_stores_boolean_true_from_string(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """dovo config set converts "true" to boolean True and stores True in config.json."""
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

    def test_config_set_cli_schema_violation_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """dovo config set exits with error panel on schema violation."""
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
        """dovo config set --format json emits NDJSON ConfigSetResult event."""
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
                "agent.model",
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
                "key": "agent.model",
                "value": "qwen2.5-coder",
                "value_str": "qwen2.5-coder",
                "value_type": "str",
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
