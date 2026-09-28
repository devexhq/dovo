"""Dual-tier matrix tests for wt config show."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from worktree.cli import app
from worktree.cli.config.commands.config_show import config_show_command
from worktree.cli.context import CliContext
from worktree.common.filesystem import Filesystem
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.config.generator import build_default_config
from worktree.core.config.loader import ConfigLoadStatus
from worktree.core.config.models import ConfigTier, WorktreeConfig
from worktree.core.db.db import WorktreeDb
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class ConfigShowRootTests:
    """Direct handler unit tests for config_show_command."""

    def test_config_show_returns_effective_config(self, isolated_workspace: Path) -> None:
        """Handler returns ConfigLoadResult with loaded configuration."""
        config_path = isolated_workspace / ".worktree" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        paths = _paths_for(isolated_workspace)
        context = CliContext(paths=paths, db=WorktreeDb(database_file=paths.database_file, project_id=paths.project_id))
        result = config_show_command(context)

        assert result.status == ConfigLoadStatus.OK
        assert result.config_path == config_path
        assert result.config == WorktreeConfig.model_validate(payload)
        assert result.raw == payload
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

    def test_config_show_missing_config_returns_not_found(self, isolated_workspace: Path) -> None:
        """Handler returns NOT_FOUND status when config.json is missing."""
        config_path = isolated_workspace / ".worktree" / "config.json"
        paths = _paths_for(isolated_workspace)
        context = CliContext(paths=paths, db=WorktreeDb(database_file=paths.database_file, project_id=paths.project_id))
        result = config_show_command(context)

        assert result.status == ConfigLoadStatus.NOT_FOUND
        assert result.config_path == config_path
        assert result.config is None
        assert result.raw is None
        assert result.errors == [f"Configuration file not found at '{config_path}' (CONFIG_NOT_FOUND)."]
        assert result.fixes == ["Run `wt init` to create `.worktree/config.json`"]
        assert result.warnings == []


class ConfigShowCliIntegrationTests:
    """Typer runner integration tests for wt config show."""

    def test_config_show_cli_renders_effective_config_terminal(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """wt config show prints header and effective JSON."""
        config_path = isolated_workspace / ".worktree" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 0
        assert "Config: " in res.stdout
        assert "Status: valid" in res.stdout
        assert '"version": 1' in res.stdout

    def test_config_show_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """wt config show --format json emits NDJSON ConfigLoadResult."""
        config_path = isolated_workspace / ".worktree" / "config.json"
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
        """wt config show exits on missing config with error panel."""
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
        """[tier-3/integration] wt config show --format json: User tier agent.model override appears in the config field; exit 0."""
        write_tier_config(ConfigTier.USER, {"agent": {"model": "user-tier-model"}})

        config_path = isolated_workspace / ".worktree" / "config.json"
        Filesystem.atomic_write_json(config_path, {"version": 1, "project": {"name": "demo-workspace"}})

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show", "--format", "json"])

        assert res.exit_code == 0
        payload = json.loads(res.stdout)
        assert payload["payload"]["config"]["agent"]["model"] == "user-tier-model"

    def test_config_show_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-3/integration] wt config show: malformed User tier config.json → exit 1, 'Config Error' and 'Invalid configuration in user layer' in stdout."""
        write_tier_config(ConfigTier.USER, "{not valid json")

        config_path = isolated_workspace / ".worktree" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))

        res = cli_runner.invoke(app, ["-p", str(isolated_workspace), "config", "show"])

        assert res.exit_code == 1
        assert "Config Error" in res.stdout
        assert "Invalid configuration in user layer" in res.stdout
