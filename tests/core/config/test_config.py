from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.models import Remediation
from dovo.core.config.config import Config
from dovo.core.config.exceptions import ConfigError, ConfigLoadDiagnostics
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import ConfigLoadStatus, load_config
from dovo.core.config.models import AgentSettings, ConfigTier, DovoConfig
from dovo.core.config.mutate import ConfigSetResult, ConfigUnsetResult
from tests.harness.diagnostic_log import diagnostic_record, read_single_event

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
        assert result.error_code == ErrorCode.CONFIG_TIER_JSON_INVALID
        assert result.errors == [
            f"Invalid configuration in {tier} tier ({tier_config_path}): "
            "Expecting property name enclosed in double quotes at line 1 column 2 (char 1)"
        ]
        assert result.remediations == []
        assert result.diagnostics == []
        assert result.config_path == tier_config_path

    def test_unreadable_tier_reports_tier_unreadable_with_one_record(
        self,
        workspace_paths: WorkspacePaths,
        write_tier_config: WriteTierConfig,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        tier_config_path = write_tier_config(ConfigTier.USER, {})
        Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config("demo-workspace"))
        real_read_text = Path.read_text

        def _denied_read(
            self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
        ) -> str:
            if self == tier_config_path:
                raise PermissionError("denied")
            return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(Path, "read_text", _denied_read)

        result = Config(workspace_paths).load()

        assert result.status == ConfigLoadStatus.TIER_INVALID
        assert result.error_code == ErrorCode.CONFIG_TIER_UNREADABLE
        assert result.errors == [
            f"Invalid configuration in user tier ({tier_config_path}): unable to read file: denied. "
            "Check file permissions and that the path is readable."
        ]
        assert result.config_path == tier_config_path
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "config.load"
        assert event["context"] == {"path": str(workspace_paths.config_file)}
        assert event["exception_type"] == "PermissionError"

    def test_unexpected_tier_error_reports_internal_error(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config("demo-workspace"))

        def _boom(paths: WorkspacePaths) -> DovoConfig:
            raise TypeError("boom")

        monkeypatch.setattr("dovo.core.config.config.load_tiered_config", _boom)

        result = Config(workspace_paths).load()

        assert result.status == ConfigLoadStatus.TIER_INVALID
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during config.load: boom"]
        assert result.config_path == workspace_paths.config_file
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert read_single_event(frozen_diagnostic_clock)["exception_type"] == "TypeError"


class ConfigLoadRequiredTests:
    @pytest.mark.parametrize("case", ["missing", "user_tier"])
    def test_load_required_raises_config_error_with_load_code(
        self, workspace_paths: WorkspacePaths, write_tier_config: WriteTierConfig, case: str
    ) -> None:
        """Raise ConfigError with the load's code, its errors in the message and its remediations, and no records."""
        if case == "missing":
            expected_code = ErrorCode.CONFIG_NOT_FOUND
            expected_errors = f"Configuration file not found at '{workspace_paths.config_file}'."
            expected_remediations = [
                Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")
            ]
        else:
            Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config("demo-workspace"))
            tier_config_path = write_tier_config(ConfigTier.USER, "{not valid json")
            expected_code = ErrorCode.CONFIG_TIER_JSON_INVALID
            expected_errors = (
                f"Invalid configuration in user tier ({tier_config_path}): "
                "Expecting property name enclosed in double quotes at line 1 column 2 (char 1)"
            )
            expected_remediations = []

        with pytest.raises(ConfigError) as exc_info:
            Config.load_required(workspace_paths)

        error = exc_info.value
        assert error.error_code == expected_code
        assert error.message == f"Failed to load config at '{workspace_paths.root_dir}': {expected_errors}"
        assert list(error.remediations) == expected_remediations
        assert "Fix:" not in error.message
        assert "Details logged:" not in error.message
        assert error.data == ConfigLoadDiagnostics(records=())

    def test_load_required_unreadable_config_carries_the_load_record(
        self, workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Raise ConfigError with CONFIG_UNREADABLE and data carrying the read failure's one record."""
        config_path = workspace_paths.config_file
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))
        real_read_text = Path.read_text

        def _denied_read(
            self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
        ) -> str:
            if self == config_path:
                raise PermissionError("denied")
            return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(Path, "read_text", _denied_read)

        with pytest.raises(ConfigError) as exc_info:
            Config.load_required(workspace_paths)

        error = exc_info.value
        assert error.error_code == ErrorCode.CONFIG_UNREADABLE
        assert error.message == (
            f"Failed to load config at '{workspace_paths.root_dir}': "
            f"Unable to read config.json at '{config_path}': denied."
        )
        assert list(error.remediations) == [Remediation(message="Check file permissions and that the path is readable")]
        assert error.data == ConfigLoadDiagnostics(records=(diagnostic_record(frozen_diagnostic_clock),))
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "config.read"


class ConfigAccessorTests:
    def test_accessor_raises_config_error_with_load_code(
        self,
        isolated_workspace: Path,
        write_tier_config: WriteTierConfig,
        workspace_paths_factory: WorkspacePathsFactory,
    ) -> None:
        write_tier_config(ConfigTier.USER, "{not valid json")
        paths = workspace_paths_factory(isolated_workspace, None)
        Filesystem.atomic_write_json(paths.config_file, build_default_config("demo-workspace"))

        with pytest.raises(ConfigError, match="Invalid configuration in user tier") as exc_info:
            _ = Config(paths).worktree

        assert exc_info.value.error_code == ErrorCode.CONFIG_TIER_JSON_INVALID


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
        with pytest.raises(ConfigError):
            _ = Config(paths).environment
        payload = build_default_config("prop-project")
        payload["environment"] = {"sensitive_variables": ["DB_PIN"]}
        Filesystem.atomic_write_json(paths.config_file, payload)

        environment = Config(paths).environment

        assert environment.sensitive_variables == ["DB_PIN"]

    def test_accessor_property_raises_config_error_when_config_missing(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(isolated_workspace, None)

        with pytest.raises(ConfigError):
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
