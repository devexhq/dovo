"""Unit tests for worktree.core.doctor.models."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from worktree.common.filesystem import WorkspacePaths
from worktree.core.config.models import ProjectConfig, WorktreeConfig
from worktree.core.doctor.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckResult,
    DoctorContext,
    DoctorReport,
)

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class ConformingCheck:
    """Protocol-conforming test double for DiagnosticCheck."""

    check_id: str = "test.check"
    name: str = "Test Check"
    category: CheckCategory = CheckCategory.GIT

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        return DiagnosticCheckResult(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=CheckStatus.OK,
            message="Check passed.",
            details={},
            duration_ms=1.5,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )


class NonConformingCheck:
    """Non-conforming test double lacking category and execute."""

    check_id: str = "non.conforming"
    name: str = "Non Conforming"


class DoctorModelsTests:
    """Unit tests for DoctorContext, DiagnosticCheckResult, and DiagnosticCheck."""

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
        """[tier-1/unit] DiagnosticCheckResult.ok: returns True for OK and SKIPPED status; returns False for FAILED and WARNING status."""
        result = DiagnosticCheckResult(
            check_id="test.status",
            name="Status Check",
            category=CheckCategory.CONFIG,
            status=status,
            message=f"Status is {status}",
            details={"key": "value"},
            duration_ms=4.2,
            error_code="TEST_ERROR" if status == CheckStatus.FAILED else None,
            errors=[f"Error: {status}"] if status == CheckStatus.FAILED else [],
            warnings=[f"Warning: {status}"] if status == CheckStatus.WARNING else [],
            fixes=[],
            remediations=[],
        )

        assert result.ok is expected_ok

    def test_doctor_context_extra_fields_forbidden(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] DoctorContext: instantiating with unknown keyword argument raises ValidationError."""
        paths = workspace_paths_factory(tmp_path, None)
        context = DoctorContext(cwd=tmp_path, config=None, paths=paths)
        assert context.cwd == tmp_path
        assert context.config is None

        # Verify strict validation forbids unknown extra fields
        with pytest.raises(ValidationError):
            DoctorContext.model_validate({"cwd": str(tmp_path), "paths": paths, "unknown_field": "disallowed"})

    def test_doctor_context_with_config(self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> None:
        """[tier-1/unit] DoctorContext: correctly holds WorktreeConfig when provided."""
        config = WorktreeConfig(version=1, project=ProjectConfig(name="test-project"))
        context = DoctorContext(cwd=tmp_path, config=config, paths=workspace_paths_factory(tmp_path, None))

        assert context.cwd == tmp_path
        assert context.config == config

    def test_diagnostic_check_protocol_runtime_checkable(self) -> None:
        """[tier-1/unit] DiagnosticCheck: verifies runtime_checkable identifies classes conforming to protocol attributes and methods."""
        conforming = ConformingCheck()
        non_conforming = NonConformingCheck()

        assert isinstance(conforming, DiagnosticCheck)
        assert not isinstance(non_conforming, DiagnosticCheck)


class DoctorReportModelsTests:
    """Unit tests for DoctorReport model and aggregate properties."""

    def test_report_ok_true_when_checks_ok_or_skipped(self, tmp_path: Path) -> None:
        """[tier-1/unit] DoctorReport.ok: returns True when all executed checks have status OK or SKIPPED."""
        check1 = DiagnosticCheckResult(
            check_id="check.one",
            name="Check One",
            category=CheckCategory.GIT,
            status=CheckStatus.OK,
            message="passed",
            details={},
            duration_ms=1.0,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )
        check2 = DiagnosticCheckResult(
            check_id="check.two",
            name="Check Two",
            category=CheckCategory.CONFIG,
            status=CheckStatus.SKIPPED,
            message="skipped",
            details={},
            duration_ms=0.0,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )
        report = DoctorReport(
            workspace_root=tmp_path,
            checks=[check1, check2],
            total_duration_ms=1.0,
        )

        assert report.ok is True
        assert report.has_warnings is False
        assert report.failed_checks == []
        assert report.warning_checks == []

    def test_report_ok_false_when_any_check_failed(self, tmp_path: Path) -> None:
        """[tier-1/unit] DoctorReport.ok: returns False when any executed check has status FAILED."""
        check_ok = DiagnosticCheckResult(
            check_id="check.ok",
            name="Check OK",
            category=CheckCategory.GIT,
            status=CheckStatus.OK,
            message="passed",
            details={},
            duration_ms=1.0,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )
        check_failed = DiagnosticCheckResult(
            check_id="check.failed",
            name="Check Failed",
            category=CheckCategory.SANDBOX,
            status=CheckStatus.FAILED,
            message="failed",
            details={"error": "broken"},
            duration_ms=2.5,
            error_code="DOCTOR_CHECK_CRASH",
            errors=["broken"],
            warnings=[],
            fixes=[],
            remediations=[],
        )
        report = DoctorReport(
            workspace_root=tmp_path,
            checks=[check_ok, check_failed],
            total_duration_ms=3.5,
        )

        assert report.ok is False
        assert report.failed_checks == [check_failed]
        assert report.warning_checks == []

    def test_report_has_warnings_true_when_warning_present(self, tmp_path: Path) -> None:
        """[tier-1/unit] DoctorReport.has_warnings: returns True when a check has status WARNING while ok remains True."""
        check_warn = DiagnosticCheckResult(
            check_id="check.warn",
            name="Check Warning",
            category=CheckCategory.ENVIRONMENT,
            status=CheckStatus.WARNING,
            message="warning",
            details={},
            duration_ms=1.2,
            error_code=None,
            errors=[],
            warnings=["disk space low"],
            fixes=[],
            remediations=[],
        )
        report = DoctorReport(
            workspace_root=tmp_path,
            checks=[check_warn],
            total_duration_ms=1.2,
        )

        assert report.ok is True
        assert report.has_warnings is True
        assert report.warning_checks == [check_warn]
        assert report.failed_checks == []

    def test_report_filtering_accessors(self, tmp_path: Path) -> None:
        """[tier-1/unit] DoctorReport.failed_checks and warning_checks: return matching DiagnosticCheckResult subsets."""
        check_ok = DiagnosticCheckResult(
            check_id="c.ok",
            name="OK",
            category=CheckCategory.GIT,
            status=CheckStatus.OK,
            message="ok",
            details={},
            duration_ms=0.5,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )
        check_warn = DiagnosticCheckResult(
            check_id="c.warn",
            name="WARN",
            category=CheckCategory.FILESYSTEM,
            status=CheckStatus.WARNING,
            message="warn",
            details={},
            duration_ms=0.5,
            error_code=None,
            errors=[],
            warnings=["warn"],
            fixes=[],
            remediations=[],
        )
        check_fail = DiagnosticCheckResult(
            check_id="c.fail",
            name="FAIL",
            category=CheckCategory.AGENT,
            status=CheckStatus.FAILED,
            message="fail",
            details={},
            duration_ms=0.5,
            error_code="CRASH",
            errors=["fail"],
            warnings=[],
            fixes=[],
            remediations=[],
        )
        check_skip = DiagnosticCheckResult(
            check_id="c.skip",
            name="SKIP",
            category=CheckCategory.CONFIG,
            status=CheckStatus.SKIPPED,
            message="skip",
            details={},
            duration_ms=0.0,
            error_code=None,
            errors=[],
            warnings=[],
            fixes=[],
            remediations=[],
        )

        report = DoctorReport(
            workspace_root=tmp_path,
            checks=[check_ok, check_warn, check_fail, check_skip],
            total_duration_ms=5.0,
        )

        assert report.ok is False
        assert report.has_warnings is True
        assert report.failed_checks == [check_fail]
        assert report.warning_checks == [check_warn]
