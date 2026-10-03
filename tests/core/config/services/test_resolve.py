from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadStatus, load_config
from dovo.core.config.models import ConfigTier, DovoConfig
from dovo.core.config.services.resolve import resolve_effective_config


class ResolveEffectiveConfigTests:
    """[tier-1/unit] Repo/Global/User precedence and error-attribution contracts for resolve_effective_config."""

    def test_repo_only_config_matches_flat_load_result(self, workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] resolve_effective_config: repo tier alone → OK, config equals DovoConfig.model_validate(repo payload), raw equals config.model_dump(mode='json')."""
        config_path = workspace_paths.config_file
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        result = resolve_effective_config(workspace_paths)

        expected_config = DovoConfig.model_validate(payload)
        assert result.status == ConfigLoadStatus.OK
        assert result.config_path == config_path
        assert result.config == expected_config
        assert result.raw == expected_config.model_dump(mode="json")
        assert result.errors == []

    def test_global_and_user_tier_overrides_merge_over_repo_and_packaged(
        self, workspace_paths: WorkspacePaths, write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path]
    ) -> None:
        """[tier-1/unit] resolve_effective_config: Global agent.temperature, User agent.model, and Repo worktree.base_ref all present in the merged DovoConfig, with Repo overriding a User worktree.base_ref."""
        write_tier_config(ConfigTier.GLOBAL, {"agent": {"temperature": 0.6}})
        write_tier_config(ConfigTier.USER, {"agent": {"model": "user-model"}, "worktree": {"base_ref": "develop"}})

        repo_payload = {
            "version": 1,
            "project": {"name": "demo-workspace"},
            "worktree": {"base_ref": "main"},
        }
        Filesystem.atomic_write_json(workspace_paths.config_file, repo_payload)

        result = resolve_effective_config(workspace_paths)

        assert result.status == ConfigLoadStatus.OK
        assert result.config is not None
        assert result.config.agent.temperature == 0.6
        assert result.config.agent.model == "user-model"
        assert result.config.worktree.base_ref == "main"

    def test_missing_repo_config_returns_not_found_without_packaged_backfill(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] resolve_effective_config: no .dovo/config.json → status NOT_FOUND, config is None, errors[0] identical to load_config's own CONFIG_NOT_FOUND message."""
        flat_result = load_config(workspace_paths)

        result = resolve_effective_config(workspace_paths)

        assert result.status == ConfigLoadStatus.NOT_FOUND
        assert result.config is None
        assert result.errors == flat_result.errors

    def test_malformed_repo_json_returns_malformed_json_unchanged(self, workspace_paths: WorkspacePaths) -> None:
        """[tier-1/unit] resolve_effective_config: malformed repo config.json → status MALFORMED_JSON, errors[0] identical to load_config's own CONFIG_MALFORMED_JSON message."""
        workspace_paths.config_file.write_text("{not valid json", encoding="utf-8")
        flat_result = load_config(workspace_paths)

        result = resolve_effective_config(workspace_paths)

        assert result.status == ConfigLoadStatus.MALFORMED_JSON
        assert result.errors == flat_result.errors

    @pytest.mark.parametrize(
        "tier", [pytest.param(ConfigTier.GLOBAL, id="global"), pytest.param(ConfigTier.USER, id="user")]
    )
    def test_invalid_global_or_user_tier_returns_tier_invalid(
        self,
        workspace_paths: WorkspacePaths,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
        tier: ConfigTier,
    ) -> None:
        """[tier-1/unit] resolve_effective_config: malformed JSON in the given tier with a valid repo tier → status TIER_INVALID, config_path equals that tier's file path, errors[0] starts with 'Invalid configuration in {tier} layer'."""
        tier_config_path = write_tier_config(tier, "{not valid json")

        Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config("demo-workspace"))

        result = resolve_effective_config(workspace_paths)

        assert result.status == ConfigLoadStatus.TIER_INVALID
        assert result.config_path == tier_config_path
        assert result.errors[0].startswith(f"Invalid configuration in {tier} layer")
