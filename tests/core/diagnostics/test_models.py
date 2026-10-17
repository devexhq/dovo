"""Unit tests for dovo.core.diagnostics.models."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import DovoConfig, ProjectConfig
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckResult,
    DiagnosticsContext,
    DiagnosticsReport,
)
from tests.harness.builders import diagnostic_result

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class StructuralCheck:
    """Conforms to DiagnosticCheck by shape alone, without inheriting it."""

    check_id: str = "structural.check"
    name: str = "Structural Check"
    category: CheckCategory = CheckCategory.GIT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        return diagnostic_result(check_id=self.check_id, name=self.name, category=self.category)


class NonConformingCheck:
    """Non-conforming test double lacking category and execute."""

    check_id: str = "non.conforming"
    name: str = "Non Conforming"


class DiagnosticsModelsTests:
    """Unit tests for DiagnosticsContext, DiagnosticCheckResult, and DiagnosticCheck."""

    @pytest.mark.parametrize(
        ("status", "expected_ok"),
        [
            pytest.param(CheckStatus.OK, True, id="ok_is_true"),
            pytest.param(CheckStatus.SKIPPED, True, id="skipped_is_true"),
            pytest.param(CheckStatus.FAILED, False, id="failed_is_false"),
            pytest.param(CheckStatus.WARNING, False, id="warning_is_false"),
        ],
    )
    def test_diagnostic_check_result_ok_property_contract(
        self,
        status: CheckStatus,
        expected_ok: bool,
    ) -> None:
        result = diagnostic_result(status=status)

        assert result.ok is expected_ok

    def test_doctor_context_extra_fields_forbidden(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        context = DiagnosticsContext(cwd=tmp_path, config=None, paths=paths)
        assert context.cwd == tmp_path
        assert context.config is None

        # Verify strict validation forbids unknown extra fields
        with pytest.raises(ValidationError):
            DiagnosticsContext.model_validate({"cwd": str(tmp_path), "paths": paths, "unknown_field": "disallowed"})

    def test_doctor_context_with_config(self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> None:
        config = DovoConfig(version=1, project=ProjectConfig(name="test-project"))
        context = DiagnosticsContext(cwd=tmp_path, config=config, paths=workspace_paths_factory(tmp_path, None))

        assert context.cwd == tmp_path
        assert context.config == config

    def test_diagnostic_check_protocol_runtime_checkable(self) -> None:
        conforming = StructuralCheck()
        non_conforming = NonConformingCheck()

        assert isinstance(conforming, DiagnosticCheck)
        assert not isinstance(non_conforming, DiagnosticCheck)


class DiagnosticsReportModelsTests:
    """Unit tests for DiagnosticsReport model and aggregate properties."""

    def test_report_ok_true_when_checks_ok_or_skipped(self, tmp_path: Path) -> None:
        check1 = diagnostic_result(status=CheckStatus.OK)
        check2 = diagnostic_result(status=CheckStatus.SKIPPED)
        report = DiagnosticsReport(
            workspace_root=tmp_path,
            checks=[check1, check2],
            total_duration_ms=1.0,
        )

        assert report.ok is True
        assert report.has_warnings is False

    def test_report_ok_false_when_any_check_failed(self, tmp_path: Path) -> None:
        check_ok = diagnostic_result(status=CheckStatus.OK)
        check_failed = diagnostic_result(status=CheckStatus.FAILED)
        report = DiagnosticsReport(
            workspace_root=tmp_path,
            checks=[check_ok, check_failed],
            total_duration_ms=3.5,
        )

        assert report.ok is False

    def test_report_has_warnings_true_when_warning_present(self, tmp_path: Path) -> None:
        check_warn = diagnostic_result(status=CheckStatus.WARNING)
        report = DiagnosticsReport(
            workspace_root=tmp_path,
            checks=[check_warn],
            total_duration_ms=1.2,
        )

        assert report.ok is True
        assert report.has_warnings is True

    def test_report_with_every_status_is_not_ok_and_has_warnings(self, tmp_path: Path) -> None:
        check_ok = diagnostic_result(status=CheckStatus.OK)
        check_warn = diagnostic_result(status=CheckStatus.WARNING)
        check_fail = diagnostic_result(status=CheckStatus.FAILED)
        check_skip = diagnostic_result(status=CheckStatus.SKIPPED)

        report = DiagnosticsReport(
            workspace_root=tmp_path,
            checks=[check_ok, check_warn, check_fail, check_skip],
            total_duration_ms=5.0,
        )

        assert report.ok is False
        assert report.has_warnings is True
