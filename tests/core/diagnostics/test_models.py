"""Unit tests for dovo.core.diagnostics.models."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import DovoConfig, ProjectConfig
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckRecord,
    DiagnosticsContext,
    DiagnosticsReport,
)
from dovo.core.git.runner import GitRunner
from tests.harness.builders import diagnostic_result

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class StructuralCheck:
    """Conforms to DiagnosticCheck by shape alone, without inheriting it."""

    check_id: str = "structural.check"
    name: str = "Structural Check"
    category: CheckCategory = CheckCategory.GIT

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckRecord:
        return diagnostic_result(check_id=self.check_id, name=self.name, category=self.category)


class NonConformingCheck:
    """Non-conforming test double lacking category and execute."""

    check_id: str = "non.conforming"
    name: str = "Non Conforming"


class DiagnosticsModelsTests:
    """Unit tests for DiagnosticsContext, DiagnosticCheckRecord, and DiagnosticCheck."""

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

    def test_warning_check_carries_its_code_with_no_errors(self) -> None:
        """Build a WARNING DOCTOR_WORKTREE_STALE record with errors == [] and warnings == ['stale']."""
        record = diagnostic_result(
            status=CheckStatus.WARNING, error_code=ErrorCode.DOCTOR_WORKTREE_STALE, warnings=["stale"]
        )

        assert (record.error_code, record.errors, record.warnings) == (ErrorCode.DOCTOR_WORKTREE_STALE, [], ["stale"])

    @pytest.mark.parametrize(
        "error_code",
        [
            pytest.param("GIT_NOT_REPO", id="unpublished-name"),
            pytest.param("DOCTOR_GIT_REPO_NOT_FOUND", id="published-value-as-plain-string"),
        ],
    )
    def test_check_with_unpublished_code_is_refused(self, error_code: str) -> None:
        """Raise ValidationError when a DiagnosticCheckRecord is built with an error_code that is not an ErrorCode member."""
        with pytest.raises(ValidationError):
            diagnostic_result(status=CheckStatus.FAILED, error_code=error_code, errors=["boom"])

    def test_failed_record_requires_error_code(self) -> None:
        """Raise ValidationError when a FAILED DiagnosticCheckRecord carries no error_code."""
        with pytest.raises(ValidationError, match="a failed check must carry an error_code"):
            DiagnosticCheckRecord(
                check_id="test.check",
                name="Test Check",
                category=CheckCategory.GIT,
                status=CheckStatus.FAILED,
                message="Check failed.",
            )

    def test_doctor_context_extra_fields_forbidden(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        context = DiagnosticsContext(cwd=tmp_path, config=None, paths=paths, git=GitRunner())
        assert context.cwd == tmp_path
        assert context.config is None

        # Verify strict validation forbids unknown extra fields
        with pytest.raises(ValidationError):
            DiagnosticsContext.model_validate(
                {"cwd": tmp_path, "paths": paths, "git": GitRunner(), "unknown_field": "disallowed"}
            )

    def test_doctor_context_with_config(self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory) -> None:
        config = DovoConfig(version=1, project=ProjectConfig(name="test-project"))
        context = DiagnosticsContext(
            cwd=tmp_path, config=config, paths=workspace_paths_factory(tmp_path, None), git=GitRunner()
        )

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
            duration_seconds=1.0,
        )

        assert report.ok is True
        assert report.has_warnings is False

    def test_report_ok_false_when_any_check_failed(self, tmp_path: Path) -> None:
        check_ok = diagnostic_result(status=CheckStatus.OK)
        check_failed = diagnostic_result(status=CheckStatus.FAILED)
        report = DiagnosticsReport(
            workspace_root=tmp_path,
            checks=[check_ok, check_failed],
            duration_seconds=3.5,
        )

        assert report.ok is False

    def test_report_has_warnings_true_when_warning_present(self, tmp_path: Path) -> None:
        check_warn = diagnostic_result(status=CheckStatus.WARNING)
        report = DiagnosticsReport(
            workspace_root=tmp_path,
            checks=[check_warn],
            duration_seconds=1.2,
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
            duration_seconds=5.0,
        )

        assert report.ok is False
        assert report.has_warnings is True

    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            pytest.param([CheckStatus.OK, CheckStatus.WARNING, CheckStatus.SKIPPED], None, id="no_failed_check"),
            pytest.param(
                [CheckStatus.WARNING, CheckStatus.FAILED, CheckStatus.FAILED],
                ErrorCode.DOCTOR_FS_UNWRITABLE,
                id="first_failed_check",
            ),
        ],
    )
    def test_error_code_is_first_failed_checks_code(
        self, tmp_path: Path, statuses: list[CheckStatus], expected: ErrorCode | None
    ) -> None:
        """Return the first FAILED check's error_code, or None; the property is not part of the dumped payload."""
        codes = iter([ErrorCode.DOCTOR_FS_UNWRITABLE, ErrorCode.DOCTOR_BINARY_MISSING])
        checks = [
            diagnostic_result(status=status, error_code=next(codes))
            if status == CheckStatus.FAILED
            else diagnostic_result(status=status)
            for status in statuses
        ]

        report = DiagnosticsReport(workspace_root=tmp_path, checks=checks)

        assert report.error_code == expected
        assert "error_code" not in report.model_dump()
