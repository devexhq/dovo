from __future__ import annotations

import copy
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.tool_policy import ToolPolicy
from dovo.core.config.exceptions import ConfigError, ConfigTierSource
from dovo.core.config.generator import CANONICAL_V1_DEFAULTS
from dovo.core.config.models import ConfigTier, DovoConfig
from dovo.core.config.tiers import (
    load_tiered_config,
    resolve_config_tiers,
)

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _write_tier_config(path: Path, payload: dict[str, Any]) -> None:
    """Write a tier config.json fixture, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _tier_config_path(tier: ConfigTier, isolated_workspace: Path, global_root: Path) -> Path:
    """Resolve the on-disk config.json path for a file-based tier (GLOBAL, USER, or REPO)."""
    global_paths = resolve_global_paths(global_root)
    if tier == ConfigTier.GLOBAL:
        return global_paths.global_dir / "config.json"
    if tier == ConfigTier.USER:
        return global_paths.user_dir / "config.json"
    return isolated_workspace / ".dovo" / "config.json"


class ConfigTierResolutionTests:
    def test_zero_optional_tiers_returns_only_packaged_tier(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"

        entries = resolve_config_tiers(workspace_paths_factory(repo_root, global_root))

        expected_data = {**CANONICAL_V1_DEFAULTS, "project": {"name": "unnamed_project", "initialized_at": None}}
        assert len(entries) == 1
        assert entries[0].tier == ConfigTier.PACKAGED
        assert entries[0].path is None
        assert entries[0].data == expected_data

    def test_all_tiers_present_returns_four_tiers_in_precedence_order(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        global_root = tmp_path / "global_home"

        global_paths = resolve_global_paths(global_root)
        global_config_path = global_paths.global_dir / "config.json"
        user_config_path = global_paths.user_dir / "config.json"
        repo_config_path = repo_root / ".dovo" / "config.json"

        global_data = {"agents": {"copilot": {"temperature": 0.5}}}
        user_data = {"agents": {"copilot": {"model": "local-llm"}}}
        repo_data = {"worktree": {"base_ref": "main"}}
        _write_tier_config(global_config_path, global_data)
        _write_tier_config(user_config_path, user_data)
        _write_tier_config(repo_config_path, repo_data)

        entries = resolve_config_tiers(workspace_paths_factory(repo_root, global_root))

        assert [entry.tier for entry in entries] == [
            ConfigTier.PACKAGED,
            ConfigTier.GLOBAL,
            ConfigTier.USER,
            ConfigTier.REPO,
        ]
        assert entries[1].path == global_config_path
        assert entries[1].data == global_data
        assert entries[2].path == user_config_path
        assert entries[2].data == user_data
        assert entries[3].path == repo_config_path
        assert entries[3].data == repo_data

    def test_missing_global_tier_silently_skipped(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        global_root = tmp_path / "global_home"

        global_paths = resolve_global_paths(global_root)
        user_config_path = global_paths.user_dir / "config.json"
        repo_config_path = repo_root / ".dovo" / "config.json"
        _write_tier_config(user_config_path, {"agents": {"copilot": {"model": "test-model"}}})
        _write_tier_config(repo_config_path, {"worktree": {"base_ref": "main"}})

        entries = resolve_config_tiers(workspace_paths_factory(repo_root, global_root))

        assert [entry.tier for entry in entries] == [ConfigTier.PACKAGED, ConfigTier.USER, ConfigTier.REPO]


class TieredConfigMergeTests:
    def test_no_tier_files_returns_packaged_defaults_as_dovo_config(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"

        config = load_tiered_config(workspace_paths_factory(repo_root, global_root))

        expected_data = {**CANONICAL_V1_DEFAULTS, "project": {"name": "unnamed_project", "initialized_at": None}}
        assert config == DovoConfig.model_validate(expected_data)

    def test_user_tier_overrides_agent_model_when_repo_leaves_unset(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"agents": {"copilot": {"model": "test-model"}}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"worktree": {"base_ref": "main"}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert config.agents["copilot"].model == "test-model"

    def test_repo_tier_overrides_worktree_base_ref_over_user_default(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"worktree": {"base_ref": "develop"}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"worktree": {"base_ref": "main"}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert config.worktree.base_ref == "main"

    def test_nested_dict_keys_merge_recursively_across_tiers(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        # Pins FR-3 (recursive dict merge): distinct from the missing-tier test below,
        # which pins FR-2 (a missing tier is non-fatal) using the same two-tier fixture shape.
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.global_dir / "config.json", {"agents": {"copilot": {"temperature": 0.5}}})
        _write_tier_config(global_paths.user_dir / "config.json", {"agents": {"copilot": {"model": "local-llm"}}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert config.agents["copilot"].temperature == 0.5
        assert config.agents["copilot"].model == "local-llm"

    def test_sensitive_variables_are_unioned_across_tiers_in_precedence_order(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(
            global_paths.global_dir / "config.json", {"environment": {"sensitive_variables": ["A", "B"]}}
        )
        _write_tier_config(global_paths.user_dir / "config.json", {"environment": {"sensitive_variables": ["B", "C"]}})
        _write_tier_config(
            isolated_workspace / ".dovo" / "config.json", {"environment": {"sensitive_variables": ["D"]}}
        )

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert config.environment.sensitive_variables == ["A", "B", "C", "D"]

    def test_tier_without_environment_section_contributes_no_names(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"environment": {"sensitive_variables": ["A"]}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"worktree": {"base_ref": "main"}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert config.environment.sensitive_variables == ["A"]

    def test_global_root_none_resolves_via_dovo_home_env(
        self,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        custom_home = tmp_path / "custom_home"
        monkeypatch.setenv("DOVO_HOME", str(custom_home))
        _write_tier_config(custom_home / "global" / "config.json", {"agents": {"copilot": {"temperature": 0.7}}})
        _write_tier_config(custom_home / "user" / "config.json", {"agents": {"copilot": {"model": "dovo-home-model"}}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, None))

        assert config.agents["copilot"].temperature == 0.7
        assert config.agents["copilot"].model == "dovo-home-model"


class AgentSettingsEnvTests:
    def test_config_without_env_keys_loads_with_defaults(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"agents": {"copilot": {}}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, tmp_path / "global_home"))

        copilot = config.agents["copilot"]
        assert (copilot.env_passthrough, copilot.env_mode) == ([], "allowlist")

    def test_repo_tier_passthrough_replaces_global_tier_passthrough(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        _write_tier_config(
            resolve_global_paths(global_root).global_dir / "config.json",
            {"agents": {"copilot": {"env_passthrough": ["A"]}}},
        )
        _write_tier_config(
            isolated_workspace / ".dovo" / "config.json",
            {"agents": {"copilot": {"env_passthrough": ["B"], "env_mode": "inherit"}}},
        )

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        copilot = config.agents["copilot"]
        assert (copilot.env_passthrough, copilot.env_mode) == (["B"], "inherit")


class AgentsMergeTests:
    def test_merge_replaces_provider_tools_whole_and_merges_other_fields(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Replace agents.claude.tools with the repo tier's object while keeping the user tier's agents.claude.model."""
        global_root = tmp_path / "global_home"
        read_rule = {"capability": "read", "pattern": "src/**"}
        deny_rule = {"capability": "write", "pattern": ".dovo/**"}
        _write_tier_config(
            resolve_global_paths(global_root).user_dir / "config.json",
            {"agents": {"claude": {"model": "user-model", "tools": {"allow": [read_rule], "allow_all": True}}}},
        )
        _write_tier_config(
            isolated_workspace / ".dovo" / "config.json", {"agents": {"claude": {"tools": {"deny": [deny_rule]}}}}
        )

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        claude = config.agents["claude"]
        assert claude.tools == ToolPolicy.model_validate({"deny": [deny_rule]})
        assert claude.model == "user-model"


class TieredConfigErrorTests:
    @pytest.mark.parametrize(
        ("content", "unreadable", "code", "details"),
        [
            pytest.param(
                "{not valid json",
                False,
                ErrorCode.CONFIG_TIER_JSON_INVALID,
                "Expecting property name enclosed in double quotes at line 1 column 2 (char 1)",
                id="malformed_json",
            ),
            pytest.param(
                "[]", False, ErrorCode.CONFIG_TIER_ROOT_INVALID, "root must be a JSON object", id="root_array"
            ),
            pytest.param(
                '{"worktree": {"max_active_worktrees": "many"}}',
                False,
                ErrorCode.CONFIG_TIER_SCHEMA_INVALID,
                "worktree.max_active_worktrees: Input should be a valid integer",
                id="type_mismatch",
            ),
            pytest.param(
                "{}",
                True,
                ErrorCode.CONFIG_TIER_UNREADABLE,
                "unable to read file: [Errno 13] Permission denied: '{path}'. "
                "Check file permissions and that the path is readable.",
                id="unreadable",
            ),
        ],
    )
    def test_tier_failure_raises_config_error_with_tier_code(
        self,
        isolated_workspace: Path,
        tmp_path: Path,
        workspace_paths_factory: WorkspacePathsFactory,
        content: str,
        unreadable: bool,
        code: ErrorCode,
        details: str,
    ) -> None:
        global_root = tmp_path / "global_home"
        tier_config_path = _tier_config_path(ConfigTier.USER, isolated_workspace, global_root)
        tier_config_path.parent.mkdir(parents=True, exist_ok=True)
        tier_config_path.write_text(content, encoding="utf-8")
        if unreadable:
            tier_config_path.chmod(0)
        try:
            if unreadable and os.access(tier_config_path, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")
            with pytest.raises(ConfigError) as exc_info:
                load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))
        finally:
            tier_config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        error = exc_info.value
        assert error.error_code == code
        assert error.message == (
            f"Invalid configuration in user tier ({tier_config_path}): {details.format(path=tier_config_path)}"
        )
        assert error.data == ConfigTierSource(path=tier_config_path)
        if unreadable:
            assert isinstance(error.__cause__, PermissionError)
        else:
            assert error.__cause__ is None

    def test_tier_with_bad_sensitive_variable_fails_without_echoing_entry(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(
            global_paths.global_dir / "config.json", {"environment": {"sensitive_variables": ["API-KEY=x"]}}
        )

        with pytest.raises(ConfigError) as exc_info:
            load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert exc_info.value.error_code == ErrorCode.CONFIG_TIER_SCHEMA_INVALID
        assert exc_info.value.message.startswith("Invalid configuration in global tier")
        assert "environment.sensitive_variables[0]" in exc_info.value.message
        assert "API-KEY=x" not in exc_info.value.message

    def test_missing_repo_config_is_silently_skipped_not_raised(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        # Pins FR-2 (a missing tier is non-fatal): distinct from the recursive-merge test
        # above, which pins FR-3 using the same two-tier fixture shape.
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.global_dir / "config.json", {"agents": {"copilot": {"temperature": 0.3}}})
        _write_tier_config(global_paths.user_dir / "config.json", {"agents": {"copilot": {"model": "user-model"}}})

        config = load_tiered_config(workspace_paths_factory(isolated_workspace, global_root))

        assert config.agents["copilot"].temperature == 0.3
        assert config.agents["copilot"].model == "user-model"


class TieredMergePurityTests:
    def test_deep_merge_does_not_mutate_source_tier_dicts(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"agents": {"copilot": {"model": "test-model"}}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"agents": {"copilot": {"temperature": 0.9}}})

        paths = workspace_paths_factory(isolated_workspace, global_root)
        entries = resolve_config_tiers(paths)
        snapshot = [copy.deepcopy(entry.data) for entry in entries]

        load_tiered_config(paths)

        assert [entry.data for entry in entries] == snapshot

    def test_merge_output_is_deterministic_regardless_of_json_key_order(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        repo_root_a = tmp_path / "repo_a"
        repo_root_a.mkdir()
        repo_root_b = tmp_path / "repo_b"
        repo_root_b.mkdir()

        (repo_root_a / ".dovo").mkdir()
        (repo_root_a / ".dovo" / "config.json").write_text(
            '{"worktree": {"base_ref": "main"}, "agents": {"copilot": {"model": "x"}}}', encoding="utf-8"
        )
        (repo_root_b / ".dovo").mkdir()
        (repo_root_b / ".dovo" / "config.json").write_text(
            '{"agents": {"copilot": {"model": "x"}}, "worktree": {"base_ref": "main"}}', encoding="utf-8"
        )

        config_a = load_tiered_config(workspace_paths_factory(repo_root_a, global_root))
        config_b = load_tiered_config(workspace_paths_factory(repo_root_b, global_root))

        assert config_a == config_b
