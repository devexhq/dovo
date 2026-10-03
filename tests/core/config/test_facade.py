from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.core.config.exceptions import ConfigLoadError
from dovo.core.config.facade import Config
from dovo.core.config.generator import build_default_config
from dovo.core.config.models import ConfigTier

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


@pytest.fixture
def workspace_paths_no_config(tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> WorkspacePaths:
    """Resolve paths for a repository whose config.json does not exist."""
    repository = tmp_path / "workspace-no-config"
    (repository / ".dovo").mkdir(parents=True)
    return workspace_paths_factory(repository, None)


class ConfigLoadTests:
    """[tier-1/unit] Config.load() routing contract."""

    def test_load_reflects_user_tier_override(
        self,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] Config.load: User tier agent.model override is present on the returned ConfigLoadResult.config."""
        write_tier_config(ConfigTier.USER, {"agent": {"model": "user-tier-model"}})
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, {"version": 1, "project": {"name": "demo-workspace"}})

        result = Config(paths).load()

        assert result.config is not None
        assert result.config.agent.model == "user-tier-model"


class ConfigLoadRequiredTests:
    def test_load_required_raises_config_load_error_on_missing_config(
        self, workspace_paths_no_config: WorkspacePaths
    ) -> None:
        """[tier-1/unit] Config.load_required: missing config.json raises ConfigLoadError with CONFIG_NOT_FOUND."""
        with pytest.raises(ConfigLoadError, match="CONFIG_NOT_FOUND"):
            Config.load_required(workspace_paths_no_config)


class ConfigLoadedConfigAccessorTests:
    """[tier-1/unit] Config._loaded_config exception contract (NFR-2)."""

    def test_loaded_config_raises_config_load_error_for_tier_invalid_status(
        self,
        isolated_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        """[tier-1/unit] Config._loaded_config: malformed User tier → raises ConfigLoadError whose message contains the tier-attributed detail text."""
        write_tier_config(ConfigTier.USER, "{not valid json")
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("demo-workspace"))

        with pytest.raises(ConfigLoadError, match="Invalid configuration in user layer"):
            _ = Config(paths)._loaded_config

    def test_loaded_config_raises_with_path_errors_and_fix_bullets_for_missing_config(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] Config._loaded_config: no config.json at all -> ConfigLoadError message includes the config path, the CONFIG_NOT_FOUND error text, and a 'Fix:' section listing the load result's fixes."""
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
        """[tier-1/unit] Config._loaded_config: a second access after a successful load does not re-read config.json from disk."""
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("cached-project"))
        config = Config(paths)

        first = config._loaded_config
        paths.config_file.write_text("{not valid json", encoding="utf-8")
        second = config._loaded_config

        assert first is second


class ConfigAccessorPropertyTests:
    """[tier-1/unit] Config's typed section accessor properties."""

    def test_version_project_and_agent_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] Config.version/.project/.agent: each returns the matching section of the loaded DovoConfig."""
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("prop-project"))
        config = Config(paths)
        loaded = config._loaded_config

        assert config.version == loaded.version
        assert config.project == loaded.project
        assert config.agent == loaded.agent

    def test_sandbox_history_doctor_prune_telemetry_concurrency_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] Config.sandbox/.history/.doctor/.prune/.telemetry/.concurrency: each returns the matching section of the loaded DovoConfig."""
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("prop-project"))
        config = Config(paths)
        loaded = config._loaded_config

        assert config.sandbox == loaded.sandbox
        assert config.history == loaded.history
        assert config.doctor == loaded.doctor
        assert config.prune == loaded.prune
        assert config.telemetry == loaded.telemetry
        assert config.concurrency == loaded.concurrency

    def test_accessor_property_raises_config_load_error_when_config_missing(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] Config.agent: accessed with no config.json present -> raises ConfigLoadError, same as ._loaded_config directly."""
        paths = workspace_paths_factory(isolated_workspace, None)

        with pytest.raises(ConfigLoadError):
            _ = Config(paths).agent


class ConfigGenerateTests:
    """[tier-1/integration] Config.generate: config.json creation, skip, and overwrite policy."""

    def test_generate_creates_config_file_with_project_name_from_root_dir(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] Config.generate: no project_name given -> created config.json's project.name defaults to the workspace root directory name."""
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
        """[tier-1/integration] Config.generate: config.json already exists, overwrite=False, repair=False -> skipped_existing=True, file left untouched."""
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
        """[tier-1/integration] Config.generate: overwrite=True on an existing config.json replaces it with the new project name and invalidates the instance's cached config."""
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("original-name"))
        config = Config(paths)
        _ = config._loaded_config

        result = config.generate(overwrite=True, project_name="replaced-name")

        assert result.overwritten is True
        assert config._loaded_config.project.name == "replaced-name"
