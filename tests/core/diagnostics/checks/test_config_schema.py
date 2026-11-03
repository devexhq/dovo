"""Unit tests for dovo.core.diagnostics.checks.config_schema."""

from collections.abc import Callable
from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.config.generator import build_default_config
from dovo.core.diagnostics.checks.config_schema import ConfigSchemaCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from dovo.core.git.runner import GitRunner

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class ConfigSchemaCheckTests:
    """Unit tests for ConfigSchemaCheck diagnostic outcomes."""

    def test_execute_missing_config_file_reports_config_not_found(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        check = ConfigSchemaCheck()
        context = DiagnosticsContext(
            cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None), git=GitRunner()
        )

        result = check.execute(context)

        config_path = isolated_workspace / ".dovo" / "config.json"
        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.FAILED
        assert result.error_code == ErrorCode.CONFIG_NOT_FOUND
        assert result.message == f"Configuration file not found at '{config_path}'."
        assert result.errors == [result.message]
        assert result.remediations == [
            Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")
        ]
        assert result.details == {}

    def test_execute_malformed_json_reports_config_json_invalid(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.write_text("{not valid json", encoding="utf-8")
        check = ConfigSchemaCheck()
        context = DiagnosticsContext(
            cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None), git=GitRunner()
        )

        result = check.execute(context)

        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.FAILED
        assert result.error_code == ErrorCode.CONFIG_JSON_INVALID
        assert "Malformed config.json" in result.message
        assert result.errors == [result.message]
        assert result.remediations == [Remediation(message="Repair JSON syntax, or restore from backup")]
        assert result.details == {}

    def test_execute_schema_invalid_payload_reports_config_schema_invalid(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, {})
        check = ConfigSchemaCheck()
        context = DiagnosticsContext(
            cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None), git=GitRunner()
        )

        result = check.execute(context)

        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.FAILED
        assert result.error_code == ErrorCode.CONFIG_SCHEMA_INVALID
        assert "Config schema validation failed" in result.message
        assert result.errors == [result.message]
        assert result.details == {"schema_errors": [result.message]}
        assert result.remediations == [
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ]

    def test_execute_config_path_directory_reports_config_path_invalid(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """Fail with CONFIG_PATH_INVALID and the load's remediation, with no schema_errors detail."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.mkdir(parents=True)
        context = DiagnosticsContext(
            cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None), git=GitRunner()
        )

        result = ConfigSchemaCheck().execute(context)

        assert result.status == CheckStatus.FAILED
        assert result.error_code == ErrorCode.CONFIG_PATH_INVALID
        assert result.errors == [f"Config path is a directory, not a file: '{config_path}'."]
        assert result.remediations == [Remediation(message="Remove the directory or point config_path at a file")]
        assert result.details == {}

    def test_execute_valid_config_returns_ok(
        self, isolated_workspace: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))
        check = ConfigSchemaCheck()
        context = DiagnosticsContext(
            cwd=isolated_workspace, paths=workspace_paths_factory(isolated_workspace, None), git=GitRunner()
        )

        result = check.execute(context)

        assert result.check_id == "config.schema"
        assert result.category == CheckCategory.CONFIG
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert "`.dovo/config.json` is present and passes schema V1 validation." in result.message
