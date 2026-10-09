from __future__ import annotations

import copy
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.config.generator import CANONICAL_V1_DEFAULTS
from dovo.core.config.models import ConfigTier, DovoConfig, HierarchicalConfigLoadStatus
from dovo.core.config.services.hierarchical_loader import (
    load_hierarchical_config,
    resolve_config_layers,
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


FILE_BASED_TIERS = [
    pytest.param(ConfigTier.GLOBAL, id="global"),
    pytest.param(ConfigTier.USER, id="user"),
    pytest.param(ConfigTier.REPO, id="repo"),
]


class ConfigLayerResolutionTests:
    """[tier-1/unit] Layer discovery contracts for resolve_config_layers."""

    def test_zero_optional_tiers_returns_only_packaged_layer(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"

        layers = resolve_config_layers(workspace_paths_factory(repo_root, global_root))

        expected_data = {**CANONICAL_V1_DEFAULTS, "project": {"name": "unnamed_project", "initialized_at": None}}
        assert len(layers) == 1
        assert layers[0].tier == ConfigTier.PACKAGED
        assert layers[0].path is None
        assert layers[0].data == expected_data

    def test_all_tiers_present_returns_four_layers_in_precedence_order(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        global_root = tmp_path / "global_home"

        global_paths = resolve_global_paths(global_root)
        global_config_path = global_paths.global_dir / "config.json"
        user_config_path = global_paths.user_dir / "config.json"
        repo_config_path = repo_root / ".dovo" / "config.json"

        global_data = {"agent": {"temperature": 0.5}}
        user_data = {"agent": {"model": "local-llm"}}
        repo_data = {"worktree": {"base_ref": "main"}}
        _write_tier_config(global_config_path, global_data)
        _write_tier_config(user_config_path, user_data)
        _write_tier_config(repo_config_path, repo_data)

        layers = resolve_config_layers(workspace_paths_factory(repo_root, global_root))

        assert [layer.tier for layer in layers] == [
            ConfigTier.PACKAGED,
            ConfigTier.GLOBAL,
            ConfigTier.USER,
            ConfigTier.REPO,
        ]
        assert layers[1].path == global_config_path
        assert layers[1].data == global_data
        assert layers[2].path == user_config_path
        assert layers[2].data == user_data
        assert layers[3].path == repo_config_path
        assert layers[3].data == repo_data

    def test_missing_global_tier_silently_skipped(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        global_root = tmp_path / "global_home"

        global_paths = resolve_global_paths(global_root)
        user_config_path = global_paths.user_dir / "config.json"
        repo_config_path = repo_root / ".dovo" / "config.json"
        _write_tier_config(user_config_path, {"agent": {"model": "test-model"}})
        _write_tier_config(repo_config_path, {"worktree": {"base_ref": "main"}})

        layers = resolve_config_layers(workspace_paths_factory(repo_root, global_root))

        assert [layer.tier for layer in layers] == [ConfigTier.PACKAGED, ConfigTier.USER, ConfigTier.REPO]


class HierarchicalConfigMergeTests:
    """[tier-1/unit] Precedence and recursive-merge contracts for load_hierarchical_config."""

    def test_no_tier_files_returns_packaged_defaults_as_dovo_config(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        repo_root = tmp_path / "repo"
        global_root = tmp_path / "global_home"

        result = load_hierarchical_config(workspace_paths_factory(repo_root, global_root))

        expected_data = {**CANONICAL_V1_DEFAULTS, "project": {"name": "unnamed_project", "initialized_at": None}}
        assert result.status == HierarchicalConfigLoadStatus.OK
        assert result.config == DovoConfig.model_validate(expected_data)

    def test_user_tier_overrides_agent_model_when_repo_leaves_unset(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"agent": {"model": "test-model"}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"worktree": {"base_ref": "main"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.config is not None
        assert result.config.agent.model == "test-model"

    def test_repo_tier_overrides_worktree_base_ref_over_user_default(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"worktree": {"base_ref": "develop"}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"worktree": {"base_ref": "main"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.config is not None
        assert result.config.worktree.base_ref == "main"

    def test_nested_dict_keys_merge_recursively_across_tiers(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        # Pins FR-3 (recursive dict merge): distinct from the missing-tier test below,
        # which pins FR-2 (a missing tier is non-fatal) using the same two-tier fixture shape.
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.global_dir / "config.json", {"agent": {"temperature": 0.5}})
        _write_tier_config(global_paths.user_dir / "config.json", {"agent": {"model": "local-llm"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.config is not None
        assert result.config.agent.temperature == 0.5
        assert result.config.agent.model == "local-llm"

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

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.config is not None
        assert result.config.environment.sensitive_variables == ["A", "B", "C", "D"]

    def test_tier_without_environment_section_contributes_no_names(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"environment": {"sensitive_variables": ["A"]}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"worktree": {"base_ref": "main"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.config is not None
        assert result.config.environment.sensitive_variables == ["A"]

    def test_global_root_none_resolves_via_dovo_home_env(
        self,
        isolated_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        custom_home = tmp_path / "custom_home"
        monkeypatch.setenv("DOVO_HOME", str(custom_home))
        _write_tier_config(custom_home / "global" / "config.json", {"agent": {"temperature": 0.7}})
        _write_tier_config(custom_home / "user" / "config.json", {"agent": {"model": "dovo-home-model"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, None))

        assert result.config is not None
        assert result.config.agent.temperature == 0.7
        assert result.config.agent.model == "dovo-home-model"


class AgentConfigEnvTests:
    """[tier-1/integration] agent.env_passthrough and agent.env_mode loading contracts for load_hierarchical_config."""

    def test_config_without_env_keys_loads_with_defaults(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] load_hierarchical_config: a repo config.json lacking both keys yields agent.env_passthrough == [] and agent.env_mode == 'allowlist'."""
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"agent": {"provider": "copilot"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, tmp_path / "global_home"))

        assert result.config is not None
        assert (result.config.agent.env_passthrough, result.config.agent.env_mode) == ([], "allowlist")

    def test_repo_tier_passthrough_replaces_global_tier_passthrough(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] load_hierarchical_config: global ['A'] and repo ['B'] resolve to agent.env_passthrough == ['B']."""
        global_root = tmp_path / "global_home"
        _write_tier_config(
            resolve_global_paths(global_root).global_dir / "config.json", {"agent": {"env_passthrough": ["A"]}}
        )
        _write_tier_config(
            isolated_workspace / ".dovo" / "config.json",
            {"agent": {"env_passthrough": ["B"], "env_mode": "inherit"}},
        )

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.config is not None
        assert (result.config.agent.env_passthrough, result.config.agent.env_mode) == (["B"], "inherit")


class HierarchicalConfigErrorTests:
    """[tier-1/unit] Tier-attributed error contracts for load_hierarchical_config."""

    @pytest.mark.parametrize("tier", FILE_BASED_TIERS)
    def test_malformed_json_in_tier_returns_malformed_json_status(
        self,
        isolated_workspace: Path,
        tmp_path: Path,
        tier: ConfigTier,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        global_root = tmp_path / "global_home"
        tier_config_path = _tier_config_path(tier, isolated_workspace, global_root)
        tier_config_path.parent.mkdir(parents=True, exist_ok=True)
        tier_config_path.write_text("{not valid json", encoding="utf-8")

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.status == HierarchicalConfigLoadStatus.MALFORMED_JSON
        assert result.tier == tier
        assert result.path == tier_config_path
        assert "line" in result.errors[0]
        assert "column" in result.errors[0]

    @pytest.mark.parametrize("tier", FILE_BASED_TIERS)
    def test_type_mismatch_in_tier_returns_validation_failed_status(
        self,
        isolated_workspace: Path,
        tmp_path: Path,
        tier: ConfigTier,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        global_root = tmp_path / "global_home"
        tier_config_path = _tier_config_path(tier, isolated_workspace, global_root)
        _write_tier_config(tier_config_path, {"worktree": {"max_active_worktrees": "many"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.status == HierarchicalConfigLoadStatus.VALIDATION_FAILED
        assert result.tier == tier
        assert result.path == tier_config_path
        assert "max_active_worktrees" in result.errors[0]

    def test_tier_with_bad_sensitive_variable_fails_without_echoing_entry(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(
            global_paths.global_dir / "config.json", {"environment": {"sensitive_variables": ["API-KEY=x"]}}
        )

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.status == HierarchicalConfigLoadStatus.VALIDATION_FAILED
        assert result.tier == ConfigTier.GLOBAL
        assert "environment.sensitive_variables[0]" in result.errors[0]
        assert "API-KEY=x" not in result.errors[0]

    @pytest.mark.parametrize("tier", FILE_BASED_TIERS)
    def test_unreadable_tier_file_returns_unreadable_status(
        self,
        isolated_workspace: Path,
        tmp_path: Path,
        tier: ConfigTier,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        global_root = tmp_path / "global_home"
        tier_config_path = _tier_config_path(tier, isolated_workspace, global_root)
        _write_tier_config(tier_config_path, {})
        tier_config_path.chmod(0)
        try:
            if os.access(tier_config_path, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")

            result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))
        finally:
            tier_config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert result.status == HierarchicalConfigLoadStatus.UNREADABLE
        assert result.tier == tier
        assert "Check file permissions and that the path is readable" in result.errors[0]

    def test_missing_repo_config_is_silently_skipped_not_raised(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        # Pins FR-2 (a missing tier is non-fatal): distinct from the recursive-merge test
        # above, which pins FR-3 using the same two-tier fixture shape.
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.global_dir / "config.json", {"agent": {"temperature": 0.3}})
        _write_tier_config(global_paths.user_dir / "config.json", {"agent": {"model": "user-model"}})

        result = load_hierarchical_config(workspace_paths_factory(isolated_workspace, global_root))

        assert result.status == HierarchicalConfigLoadStatus.OK
        assert result.config is not None
        assert result.config.agent.temperature == 0.3
        assert result.config.agent.model == "user-model"


class HierarchicalMergePurityTests:
    """[tier-1/unit] NFR-1/NFR-2 purity and determinism contracts for the merge pipeline."""

    def test_deep_merge_does_not_mutate_source_tier_dicts(
        self, isolated_workspace: Path, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        global_root = tmp_path / "global_home"
        global_paths = resolve_global_paths(global_root)
        _write_tier_config(global_paths.user_dir / "config.json", {"agent": {"model": "test-model"}})
        _write_tier_config(isolated_workspace / ".dovo" / "config.json", {"agent": {"temperature": 0.9}})

        paths = workspace_paths_factory(isolated_workspace, global_root)
        layers = resolve_config_layers(paths)
        snapshot = [copy.deepcopy(layer.data) for layer in layers]

        load_hierarchical_config(paths)

        for layer, before in zip(layers, snapshot, strict=True):
            assert layer.data == before

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
            '{"worktree": {"base_ref": "main"}, "agent": {"model": "x"}}', encoding="utf-8"
        )
        (repo_root_b / ".dovo").mkdir()
        (repo_root_b / ".dovo" / "config.json").write_text(
            '{"agent": {"model": "x"}, "worktree": {"base_ref": "main"}}', encoding="utf-8"
        )

        result_a = load_hierarchical_config(workspace_paths_factory(repo_root_a, global_root))
        result_b = load_hierarchical_config(workspace_paths_factory(repo_root_b, global_root))

        assert result_a.config == result_b.config
