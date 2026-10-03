"""Unit tests for dovo.core.doctor.checks.config_schema."""

from collections.abc import Callable
from pathlib import Path

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.core.config.generator import build_default_config
from dovo.core.doctor.checks.config_schema import ConfigSchemaCheck
from dovo.core.doctor.models import CheckCategory, CheckStatus, DoctorContext

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class ConfigSchemaCheckTests:
    """Unit tests for ConfigSchemaCheck diagnostic outcomes."""

    def test_execute_missing_config_file_returns_not_found_failure(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] ConfigSchemaCheck.execute: no .dovo/config.json -> FAILED with DOCTOR_CONFIG_NOT_FOUND."""
        check = ConfigSchemaCheck()
        context = DoctorContext(cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None))

        result = check.execute(context)

        config_path = isolated_workspace / ".dovo" / "config.json"
        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_CONFIG_NOT_FOUND"
        assert f"Configuration file not found at '{config_path}'" in result.message
        assert result.errors == [result.message]

    def test_execute_malformed_json_returns_malformed_failure(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] ConfigSchemaCheck.execute: invalid JSON syntax -> FAILED with DOCTOR_CONFIG_MALFORMED."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.write_text("{not valid json", encoding="utf-8")
        check = ConfigSchemaCheck()
        context = DoctorContext(cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None))

        result = check.execute(context)

        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_CONFIG_MALFORMED"
        assert "Malformed config.json" in result.message
        assert result.errors == [result.message]

    def test_execute_schema_invalid_payload_returns_schema_invalid_failure(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] ConfigSchemaCheck.execute: valid JSON missing required sections -> FAILED with DOCTOR_CONFIG_SCHEMA_INVALID."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, {})
        check = ConfigSchemaCheck()
        context = DoctorContext(cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None))

        result = check.execute(context)

        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_CONFIG_SCHEMA_INVALID"
        assert "Config schema validation failed" in result.message
        assert result.errors == [result.message]
        assert result.details == {"schema_errors": [result.message]}

    def test_execute_valid_config_returns_ok(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] ConfigSchemaCheck.execute: schema-valid config.json -> OK with no errors."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))
        check = ConfigSchemaCheck()
        context = DoctorContext(cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None))

        result = check.execute(context)

        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert "`.dovo/config.json` is present and passes schema V1 validation." in result.message
