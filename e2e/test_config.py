"""End-to-end tests for dovo config management, hierarchy, and redaction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class ConfigCliTests:
    """E2E tests for configuration resolution precedence, updates, validation, and masking."""

    def test_hierarchical_config_resolution_precedence(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """User config (7) overrides global (5); a repo override (9) then wins over both."""
        global_config_dir = DOVO_HOME / "global"
        user_config_dir = DOVO_HOME / "user"
        global_config_dir.mkdir(parents=True, exist_ok=True)
        user_config_dir.mkdir(parents=True, exist_ok=True)

        (global_config_dir / "config.json").write_text(
            json.dumps({"worktree": {"max_active_worktrees": 5}}), encoding="utf-8"
        )
        (user_config_dir / "config.json").write_text(
            json.dumps({"worktree": {"max_active_worktrees": 7}}), encoding="utf-8"
        )

        # Remove key from repo config so it falls through to user tier
        repo_config_file = initialized_project / ".dovo" / "config.json"
        repo_config_data = json.loads(repo_config_file.read_text("utf-8"))
        repo_config_data["worktree"].pop("max_active_worktrees", None)
        repo_config_file.write_text(json.dumps(repo_config_data), encoding="utf-8")

        # Before repo override: user config (7) overrides global config (5)
        result_user_level = run_dovo(["config", "show", "--format", "json"], cwd=initialized_project)
        assert result_user_level.exit_code == 0
        envelope_user = json.loads(result_user_level.stdout)
        assert envelope_user["payload"]["config"]["worktree"]["max_active_worktrees"] == 7

        # Apply repo override
        run_dovo(["config", "set", "worktree.max_active_worktrees", "9"], cwd=initialized_project)

        result_repo_level = run_dovo(["config", "show", "--format", "json"], cwd=initialized_project)
        assert result_repo_level.exit_code == 0
        envelope_repo = json.loads(result_repo_level.stdout)
        assert envelope_repo["payload"]["config"]["worktree"]["max_active_worktrees"] == 9

    def test_fresh_session_observes_clean_isolated_configuration(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """A newly initialized project sees schema defaults, not host ambient settings."""
        result = run_dovo(["config", "show", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        worktree_cfg = envelope["payload"]["config"]["worktree"]

        # Default schema baseline is 3
        assert worktree_cfg["max_active_worktrees"] == 3

    def test_config_show_json_displays_full_normalized_configuration(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        result = run_dovo(["config", "show", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "ConfigLoadResult"
        payload = envelope.get("payload", {})

        assert payload.get("status") == "ok"
        assert "config" in payload
        config_data = payload["config"]
        assert "project" in config_data
        assert "worktree" in config_data
        assert "agents" in config_data
        assert "history" in config_data
        assert "environment" in config_data

    def test_config_set_updates_nested_dot_path(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        result_set = run_dovo(["config", "set", "agents.copilot.model", "custom-model-e2e"], cwd=initialized_project)
        assert result_set.exit_code == 0

        config_file = initialized_project / ".dovo" / "config.json"
        with open(config_file, encoding="utf-8") as f:
            persisted = json.load(f)

        assert persisted["agents"]["copilot"]["model"] == "custom-model-e2e"

    def test_config_unset_reverts_to_schema_default(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        run_dovo(["config", "set", "agents.copilot.model", "temporary-model"], cwd=initialized_project)
        result_unset = run_dovo(["config", "unset", "agents.copilot.model"], cwd=initialized_project)

        assert result_unset.exit_code == 0
        config_file = initialized_project / ".dovo" / "config.json"
        with open(config_file, encoding="utf-8") as f:
            persisted = json.load(f)

        assert persisted["agents"] == {"copilot": {}}

        result_show = run_dovo(["config", "show", "--format", "json"], cwd=initialized_project)
        envelope = json.loads(result_show.stdout)
        assert envelope["payload"]["config"]["agents"]["copilot"]["model"] is None

    def test_config_validate_verifies_valid_and_invalid_schemas(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        valid_result = run_dovo(["config", "validate"], cwd=initialized_project)
        assert valid_result.exit_code == 0
        assert "Status: valid" in valid_result.stdout

        # Corrupt the config with schema violations
        config_file = initialized_project / ".dovo" / "config.json"
        config_file.write_text(
            json.dumps({"version": 1, "unknown_field": True, "worktree": "invalid"}),
            encoding="utf-8",
        )

        invalid_result = run_dovo(["config", "validate"], cwd=initialized_project)
        assert invalid_result.exit_code == 1
        assert "Config Validation Failed" in invalid_result.stdout or "CONFIG_SCHEMA_INVALID" in invalid_result.stdout

    def test_config_show_masks_configured_sensitive_variables(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        secret_value = "super-confidential-token-998877"
        run_dovo(["config", "set", "environment.sensitive_variables", '["SECRET_KEY"]'], cwd=initialized_project)

        result = run_dovo(
            ["config", "show"],
            cwd=initialized_project,
            env={"SECRET_KEY": secret_value},
        )

        assert result.exit_code == 0
        assert secret_value not in result.stdout
        assert secret_value not in result.stderr
