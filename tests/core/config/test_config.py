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
from dovo.core.config.models import AgentSettings, ConfigTier

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


@pytest.fixture
def workspace_paths_no_config(tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> WorkspacePaths:
    """Resolve paths for a repository whose config.json does not exist."""
    repository = tmp_path / "workspace-no-config"
    (repository / ".dovo").mkdir(parents=True)
    return workspace_paths_factory(repository, None)


class ConfigLoadTests:
    def test_load_reflects_user_tier_override(
        self,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        write_tier_config(ConfigTier.USER, {"agents": {"copilot": {"model": "user-tier-model"}}})
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, {"version": 1, "project": {"name": "demo-workspace"}})

        result = Config(paths).load()

        assert result.config is not None
        assert result.config.agents["copilot"].model == "user-tier-model"


class ConfigLoadRequiredTests:
    def test_load_required_raises_config_load_error_on_missing_config(
        self, workspace_paths_no_config: WorkspacePaths
    ) -> None:
        with pytest.raises(ConfigLoadError, match="CONFIG_NOT_FOUND"):
            Config.load_required(workspace_paths_no_config)


class ConfigLoadedConfigAccessorTests:
    def test_loaded_config_raises_config_load_error_for_tier_invalid_status(
        self,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        write_tier_config(ConfigTier.USER, "{not valid json")
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("demo-workspace"))

        with pytest.raises(ConfigLoadError, match="Invalid configuration in user layer"):
            _ = Config(paths)._loaded_config

    def test_loaded_config_raises_with_path_errors_and_fix_bullets_for_missing_config(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)

        with pytest.raises(ConfigLoadError) as exc_info:
            _ = Config(paths)._loaded_config

        message = str(exc_info.value)
        assert str(paths.root_dir) in message
        assert "CONFIG_NOT_FOUND" in message
        assert "Fix:\n- Run `dovo init` to create `.dovo/config.json`" in message

    def test_loaded_config_is_cached_after_first_successful_load(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("cached-project"))
        config = Config(paths)

        first = config._loaded_config
        paths.config_file.write_text("{not valid json", encoding="utf-8")
        second = config._loaded_config

        assert first is second


class ConfigAccessorPropertyTests:
    def test_version_and_project_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("prop-project"))
        config = Config(paths)
        loaded = config._loaded_config

        assert config.version == loaded.version
        assert config.project == loaded.project

    def test_worktree_history_doctor_prune_telemetry_concurrency_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("prop-project"))
        config = Config(paths)
        loaded = config._loaded_config

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


class ConfigGenerateTests:
    def test_generate_creates_config_file_with_project_name_from_root_dir(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)

        result = Config(paths).generate()

        assert result.created is True
        assert paths.config_file.is_file()
        loaded = Config(paths).load().config
        assert loaded is not None
        assert loaded.project.name == paths.root_dir.name

    def test_generate_without_overwrite_or_repair_skips_an_existing_config(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("original-name"))

        result = Config(paths).generate(project_name="new-name")

        assert result.skipped_existing is True
        loaded = Config(paths).load().config
        assert loaded is not None
        assert loaded.project.name == "original-name"

    def test_generate_with_overwrite_replaces_existing_config_and_clears_cache(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("original-name"))
        config = Config(paths)
        _ = config._loaded_config

        result = config.generate(overwrite=True, project_name="replaced-name")

        assert result.overwritten is True
        assert config._loaded_config.project.name == "replaced-name"


class AgentSettingsMcpServersTests:
    def test_config_with_invalid_server_name_fails_validation(self) -> None:
        with pytest.raises(ValidationError, match="'a__b'"):
            AgentSettings(mcp_servers={"a__b": McpServerDefinition(command="npx")})
