from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from worktree.common.filesystem import Filesystem
from worktree.core.config.exceptions import ConfigLoadError
from worktree.core.config.facade import Config
from worktree.core.config.generator import build_default_config
from worktree.core.config.models import ConfigTier


class ConfigLoadTests:
    """[tier-1/unit] Config.load() routing contract."""

    def test_load_reflects_user_tier_override(
        self, isolated_workspace: Path, write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path]
    ) -> None:
        """[tier-1/unit] Config.load: User tier agent.model override is present on the returned ConfigLoadResult.config."""
        write_tier_config(ConfigTier.USER, {"agent": {"model": "user-tier-model"}})
        Filesystem.atomic_write_json(
            isolated_workspace / ".worktree" / "config.json",
            {"version": 1, "project": {"name": "demo-workspace"}},
        )

        result = Config(isolated_workspace).load()

        assert result.config is not None
        assert result.config.agent.model == "user-tier-model"


class ConfigLoadedConfigAccessorTests:
    """[tier-1/unit] Config._loaded_config exception contract (NFR-2)."""

    def test_loaded_config_raises_config_load_error_for_tier_invalid_status(
        self, isolated_workspace: Path, write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path]
    ) -> None:
        """[tier-1/unit] Config._loaded_config: malformed User tier → raises ConfigLoadError whose message contains the tier-attributed detail text."""
        write_tier_config(ConfigTier.USER, "{not valid json")
        Filesystem.atomic_write_json(
            isolated_workspace / ".worktree" / "config.json", build_default_config("demo-workspace")
        )

        with pytest.raises(ConfigLoadError, match="Invalid configuration in user layer"):
            _ = Config(isolated_workspace)._loaded_config

    def test_loaded_config_raises_with_path_errors_and_fix_bullets_for_missing_config(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/unit] Config._loaded_config: no config.json at all -> ConfigLoadError message includes the config path, the CONFIG_NOT_FOUND error text, and a 'Fix:' section listing the load result's fixes."""
        with pytest.raises(ConfigLoadError) as exc_info:
            _ = Config(isolated_workspace)._loaded_config

        message = str(exc_info.value)
        assert str(isolated_workspace) in message
        assert "CONFIG_NOT_FOUND" in message
        assert "Fix:\n- Run `wt init` to create `.worktree/config.json`" in message

    def test_loaded_config_is_cached_after_first_successful_load(self, isolated_workspace: Path) -> None:
        """[tier-1/unit] Config._loaded_config: a second access after a successful load does not re-read config.json from disk."""
        config_path = isolated_workspace / ".worktree" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("cached-project"))
        config = Config(isolated_workspace)

        first = config._loaded_config
        config_path.write_text("{not valid json", encoding="utf-8")
        second = config._loaded_config

        assert first is second


class ConfigAccessorPropertyTests:
    """[tier-1/unit] Config's typed section accessor properties."""

    def test_version_project_and_agent_properties_expose_loaded_config_sections(self, isolated_workspace: Path) -> None:
        """[tier-1/unit] Config.version/.project/.agent: each returns the matching section of the loaded WorktreeConfig."""
        Filesystem.atomic_write_json(
            isolated_workspace / ".worktree" / "config.json", build_default_config("prop-project")
        )
        config = Config(isolated_workspace)
        loaded = config._loaded_config

        assert config.version == loaded.version
        assert config.project == loaded.project
        assert config.agent == loaded.agent

    def test_sandbox_history_doctor_prune_telemetry_concurrency_properties_expose_loaded_config_sections(
        self, isolated_workspace: Path
    ) -> None:
        """[tier-1/unit] Config.sandbox/.history/.doctor/.prune/.telemetry/.concurrency: each returns the matching section of the loaded WorktreeConfig."""
        Filesystem.atomic_write_json(
            isolated_workspace / ".worktree" / "config.json", build_default_config("prop-project")
        )
        config = Config(isolated_workspace)
        loaded = config._loaded_config

        assert config.sandbox == loaded.sandbox
        assert config.history == loaded.history
        assert config.doctor == loaded.doctor
        assert config.prune == loaded.prune
        assert config.telemetry == loaded.telemetry
        assert config.concurrency == loaded.concurrency

    def test_accessor_property_raises_config_load_error_when_config_missing(self, isolated_workspace: Path) -> None:
        """[tier-1/unit] Config.agent: accessed with no config.json present -> raises ConfigLoadError, same as ._loaded_config directly."""
        with pytest.raises(ConfigLoadError):
            _ = Config(isolated_workspace).agent


class ConfigGenerateTests:
    """[tier-1/integration] Config.generate: config.json creation, skip, and overwrite policy."""

    def test_generate_creates_config_file_with_project_name_from_root_dir(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Config.generate: no project_name given -> created config.json's project.name defaults to the workspace root directory name."""
        result = Config(isolated_workspace).generate()

        assert result.created is True
        config_path = isolated_workspace / ".worktree" / "config.json"
        assert config_path.is_file()
        loaded = Config(isolated_workspace).load().config
        assert loaded is not None
        assert loaded.project.name == isolated_workspace.name

    def test_generate_without_overwrite_or_repair_skips_an_existing_config(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Config.generate: config.json already exists, overwrite=False, repair=False -> skipped_existing=True, file left untouched."""
        config_path = isolated_workspace / ".worktree" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("original-name"))

        result = Config(isolated_workspace).generate(project_name="new-name")

        assert result.skipped_existing is True
        loaded = Config(isolated_workspace).load().config
        assert loaded is not None
        assert loaded.project.name == "original-name"

    def test_generate_with_overwrite_replaces_existing_config_and_clears_cache(self, isolated_workspace: Path) -> None:
        """[tier-1/integration] Config.generate: overwrite=True on an existing config.json replaces it with the new project name and invalidates the instance's cached config."""
        config_path = isolated_workspace / ".worktree" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("original-name"))
        config = Config(isolated_workspace)
        _ = config._loaded_config

        result = config.generate(overwrite=True, project_name="replaced-name")

        assert result.overwritten is True
        assert config._loaded_config.project.name == "replaced-name"
