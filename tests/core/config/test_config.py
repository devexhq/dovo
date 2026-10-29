from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.mcp_servers import McpServerDefinition
from dovo.core.config.config import Config
from dovo.core.config.exceptions import ConfigLoadError
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadStatus, load_config
from dovo.core.config.models import AgentSettings, ConfigTier, DovoConfig
from dovo.core.config.mutate import ConfigSetResult, ConfigUnsetResult

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]
WriteTierConfig = Callable[[ConfigTier, dict[str, Any] | str], Path]
ConfigMutation = Callable[[Config], ConfigSetResult | ConfigUnsetResult]


@pytest.fixture
def workspace_paths_no_config(tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> WorkspacePaths:
    """Resolve paths for a repository whose config.json does not exist."""
    repository = tmp_path / "workspace-no-config"
    (repository / ".dovo").mkdir(parents=True)
    return workspace_paths_factory(repository, None)


class ConfigLoadTests:
    def test_repo_only_config_matches_flat_load_result(self, workspace_paths: WorkspacePaths) -> None:
        config_path = workspace_paths.config_file
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        result = Config(workspace_paths).load()

        expected_config = DovoConfig.model_validate(payload)
        assert result.status == ConfigLoadStatus.OK
        assert result.config_path == config_path
        assert result.config == expected_config
        assert result.raw == expected_config.model_dump(mode="json")
        assert result.errors == []

    def test_global_and_user_tier_overrides_merge_over_repo_and_packaged(
        self, workspace_paths: WorkspacePaths, write_tier_config: WriteTierConfig
    ) -> None:
        write_tier_config(ConfigTier.GLOBAL, {"agents": {"copilot": {"temperature": 0.6}}})
        write_tier_config(
            ConfigTier.USER, {"agents": {"copilot": {"model": "user-model"}}, "worktree": {"base_ref": "develop"}}
        )

        repo_payload = {
            "version": 1,
            "project": {"name": "demo-workspace"},
            "worktree": {"base_ref": "main"},
        }
        Filesystem.atomic_write_json(workspace_paths.config_file, repo_payload)

        result = Config(workspace_paths).load()

        assert result.status == ConfigLoadStatus.OK
        assert result.config is not None
        assert result.config.agents["copilot"].temperature == 0.6
        assert result.config.agents["copilot"].model == "user-model"
        assert result.config.worktree.base_ref == "main"

    @pytest.mark.parametrize(
        ("repo_content", "expected_status"),
        [
            pytest.param(None, ConfigLoadStatus.NOT_FOUND, id="missing"),
            pytest.param("{not valid json", ConfigLoadStatus.MALFORMED_JSON, id="malformed"),
            pytest.param({"version": 1}, ConfigLoadStatus.SCHEMA_INVALID, id="schema_invalid"),
        ],
    )
    def test_repo_failure_returns_repo_status_without_reading_tiers(
        self,
        workspace_paths: WorkspacePaths,
        write_tier_config: WriteTierConfig,
        repo_content: dict[str, int] | str | None,
        expected_status: ConfigLoadStatus,
    ) -> None:
        write_tier_config(ConfigTier.USER, "{not valid json")
        if isinstance(repo_content, str):
            Filesystem.atomic_write_text(workspace_paths.config_file, repo_content)
        elif repo_content is not None:
            Filesystem.atomic_write_json(workspace_paths.config_file, repo_content)
        flat_result = load_config(workspace_paths)

        result = Config(workspace_paths).load()

        assert result.status == expected_status
        assert result.config is None
        assert result.config_path == workspace_paths.config_file
        assert result.errors == flat_result.errors

    @pytest.mark.parametrize(
        "tier", [pytest.param(ConfigTier.GLOBAL, id="global"), pytest.param(ConfigTier.USER, id="user")]
    )
    def test_invalid_global_or_user_tier_returns_tier_invalid(
        self,
        workspace_paths: WorkspacePaths,
        write_tier_config: WriteTierConfig,
        tier: ConfigTier,
    ) -> None:
        tier_config_path = write_tier_config(tier, "{not valid json")

        Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config("demo-workspace"))

        result = Config(workspace_paths).load()

        assert result.status == ConfigLoadStatus.TIER_INVALID
        assert result.config_path == tier_config_path
        assert result.errors[0].startswith(f"Invalid configuration in {tier} tier")


class ConfigLoadRequiredTests:
    def test_load_required_raises_config_load_error_on_missing_config(
        self, workspace_paths_no_config: WorkspacePaths
    ) -> None:
        with pytest.raises(ConfigLoadError, match="Configuration file not found at"):
            Config.load_required(workspace_paths_no_config)


class ConfigAccessorTests:
    def test_accessor_raises_config_load_error_with_failed_result(
        self,
        isolated_workspace: Path,
        write_tier_config: WriteTierConfig,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        write_tier_config(ConfigTier.USER, "{not valid json")
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("demo-workspace"))

        with pytest.raises(ConfigLoadError, match="Invalid configuration in user tier") as exc_info:
            _ = Config(paths).worktree

        assert exc_info.value.result.status == ConfigLoadStatus.TIER_INVALID

    def test_accessor_raises_with_path_errors_and_fix_bullets_for_missing_config(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)

        with pytest.raises(ConfigLoadError) as exc_info:
            _ = Config(paths).worktree

        message = str(exc_info.value)
        assert str(paths.root_dir) in message
        assert "Configuration file not found at" in message
        assert "Fix:\n- Run 'dovo init' to create '.dovo/config.json'" in message


class ConfigAccessorPropertyTests:
    def test_version_and_project_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("prop-project"))
        config = Config(paths)
        loaded = config.load().config
        assert loaded is not None

        assert config.version == loaded.version
        assert config.project == loaded.project

    def test_worktree_history_doctor_prune_telemetry_concurrency_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("prop-project"))
        config = Config(paths)
        loaded = config.load().config
        assert loaded is not None

        assert config.worktree == loaded.worktree
        assert config.history == loaded.history
        assert config.doctor == loaded.doctor
        assert config.prune == loaded.prune
        assert config.telemetry == loaded.telemetry
        assert config.concurrency == loaded.concurrency

    def test_environment_property_exposes_loaded_config_section(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        with pytest.raises(ConfigLoadError):
            _ = Config(paths).environment
        payload = build_default_config("prop-project")
        payload["environment"] = {"sensitive_variables": ["DB_PIN"]}
        Filesystem.atomic_write_json(paths.config_file, payload)

        environment = Config(paths).environment

        assert environment.sensitive_variables == ["DB_PIN"]

    def test_accessor_property_raises_config_load_error_when_config_missing(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)

        with pytest.raises(ConfigLoadError):
            _ = Config(paths).history


def _write_config_with_base_ref(paths: WorkspacePaths, base_ref: str) -> None:
    payload = build_default_config("cached-project")
    payload["worktree"]["base_ref"] = base_ref
    Filesystem.atomic_write_json(paths.config_file, payload)


class ConfigCacheTests:
    @pytest.mark.parametrize(
        ("mutate", "expected_base_ref"),
        [
            pytest.param(lambda config: config.set("worktree.base_ref", "main"), "main", id="set"),
            pytest.param(lambda config: config.unset("worktree.base_ref"), "HEAD", id="unset"),
        ],
    )
    def test_successful_mutation_clears_cached_config(
        self,
        isolated_workspace: Path,
        workspace_paths_factory: WorkspacePathsFactory,
        mutate: ConfigMutation,
        expected_base_ref: str,
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        _write_config_with_base_ref(paths, "develop")
        config = Config(paths)
        assert config.worktree.base_ref == "develop"

        result = mutate(config)

        assert result.ok
        assert config.worktree.base_ref == expected_base_ref

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda config: config.set("worktree.base_ref", "main"), id="set"),
            pytest.param(lambda config: config.unset("worktree.base_ref"), id="unset"),
        ],
    )
    def test_failed_mutation_keeps_cached_config(
        self,
        isolated_workspace: Path,
        workspace_paths_factory: WorkspacePathsFactory,
        mutate: ConfigMutation,
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        _write_config_with_base_ref(paths, "develop")
        config = Config(paths)
        assert config.worktree.base_ref == "develop"
        Filesystem.atomic_write_text(paths.config_file, "{not valid json")

        result = mutate(config)

        assert not result.ok
        assert config.worktree.base_ref == "develop"


class AgentSettingsMcpServersTests:
    def test_config_with_invalid_server_name_fails_validation(self) -> None:
        with pytest.raises(ValidationError, match="'a__b'"):
            AgentSettings(mcp_servers={"a__b": McpServerDefinition(command="npx")})
