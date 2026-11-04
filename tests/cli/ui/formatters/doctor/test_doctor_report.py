"""Tier 2 presentation contracts for DoctorReportFormatter."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.cli.ui.formatters.doctor import DoctorCheckView, DoctorReportFormatter, DoctorReportView
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.diagnostics import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckRecord,
    DiagnosticsReport,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

ROOT = Path("/workspace/my-repo")
NO_MODEL_MESSAGE = (
    "Run 'dovo config set agents.copilot.model \"<model>\"' to set 'agents.copilot.model' in "
    "'.dovo/config.json' to a model supported by that provider."
)
CONFIG_NOT_FOUND_MESSAGE = "Run 'dovo init' to create '.dovo/config.json'"

OK_CHECK = DiagnosticCheckRecord(
    check_id="git.repo",
    name="Git Repository Check",
    category=CheckCategory.GIT,
    status=CheckStatus.OK,
    message="Git repository detected at '/workspace/my-repo' on branch 'main'.",
    details={"root": "/workspace/my-repo", "branch": "main"},
    duration_seconds=0.0021,
    error_code=None,
    errors=[],
    warnings=[],
    remediations=[],
)

WARNING_CHECK = DiagnosticCheckRecord(
    check_id="agent.setup",
    name="Agent Setup Check",
    category=CheckCategory.AGENT,
    status=CheckStatus.WARNING,
    message="Agent provider 'copilot' has no model configured.",
    details={"provider": "copilot"},
    duration_seconds=0.0005,
    error_code=ErrorCode.DOCTOR_AGENT_MODEL_MISSING,
    errors=[],
    warnings=["Agent provider 'copilot' has no model configured."],
    remediations=[
        Remediation(
            message=NO_MODEL_MESSAGE,
            command='dovo config set agents.copilot.model "<model>"',
        )
    ],
)

FAILED_CHECK = DiagnosticCheckRecord(
    check_id="config.schema",
    name="Config Schema Check",
    category=CheckCategory.CONFIG,
    status=CheckStatus.FAILED,
    message="Configuration file not found at '/workspace/my-repo/.dovo/config.json'.",
    details={},
    duration_seconds=0.0003,
    error_code=ErrorCode.CONFIG_NOT_FOUND,
    errors=["Configuration file not found at '/workspace/my-repo/.dovo/config.json'."],
    warnings=[],
    remediations=[Remediation(message=CONFIG_NOT_FOUND_MESSAGE, command="dovo init")],
)

SKIPPED_CHECK = DiagnosticCheckRecord(
    check_id="worktree.refs",
    name="Worktree References Check",
    category=CheckCategory.WORKTREE,
    status=CheckStatus.SKIPPED,
    message="Check 'worktree.refs' skipped by configuration.",
    details={},
    duration_seconds=0.0,
    error_code=None,
    errors=[],
    warnings=[],
    remediations=[],
)


def _check_view(check: DiagnosticCheckRecord) -> DoctorCheckView:
    """Build the expected DoctorCheckView for a DiagnosticCheckRecord, field for field."""
    return DoctorCheckView(
        check_id=check.check_id,
        name=check.name,
        category=check.category,
        status=check.status,
        message=check.message,
        details=check.details,
        duration_seconds=check.duration_seconds,
        error_code=check.error_code,
        errors=check.errors,
        warnings=check.warnings,
        remediations=check.remediations,
        diagnostics=check.diagnostics,
    )


FAILED_HEADER: dict[str, object] = {
    "error_code": ErrorCode.DOCTOR_CHECKS_FAILED,
    "errors": [FAILED_CHECK.message],
    "warnings": [WARNING_CHECK.message],
    "remediations": [*FAILED_CHECK.remediations, *WARNING_CHECK.remediations],
}

MIXED_STATUS = FormatterCase(
    data=DiagnosticsReport.model_validate(
        {
            **FAILED_HEADER,
            "workspace_root": ROOT,
            "checks": [OK_CHECK, WARNING_CHECK, FAILED_CHECK, SKIPPED_CHECK],
            "duration_seconds": 0.0124,
        }
    ),
    view=DoctorReportView(
        ok=False,
        error_code=ErrorCode.DOCTOR_CHECKS_FAILED,
        errors=[FAILED_CHECK.message],
        warnings=[WARNING_CHECK.message],
        remediations=[*FAILED_CHECK.remediations, *WARNING_CHECK.remediations],
        diagnostics=[],
        workspace_root=ROOT,
        duration_seconds=0.0124,
        checks=[_check_view(c) for c in (OK_CHECK, WARNING_CHECK, FAILED_CHECK, SKIPPED_CHECK)],
    ),
    render_expectations=[
        "git.repo",
        "OK",
        "agent.setup",
        "WARNING",
        "config.schema",
        "FAILED",
        "worktree.refs",
        "SKIPPED",
        "4 checks: 1 ok, 1 warning, 1 failed (0.012s)",
        "Fix:",
        f"- {NO_MODEL_MESSAGE}",
        f"- {CONFIG_NOT_FOUND_MESSAGE}",
    ],
)

EMPTY_CHECKS = FormatterCase(
    data=DiagnosticsReport(workspace_root=ROOT, checks=[], duration_seconds=0.0),
    view=DoctorReportView(
        ok=True,
        error_code=None,
        errors=[],
        warnings=[],
        remediations=[],
        diagnostics=[],
        workspace_root=ROOT,
        duration_seconds=0.0,
        checks=[],
    ),
    render_expectations=["0 checks: 0 ok, 0 warning, 0 failed (0.000s)"],
)

DOCTOR_REPORT_CASES = [
    pytest.param(MIXED_STATUS, id="mixed_status"),
    pytest.param(EMPTY_CHECKS, id="empty_checks"),
]

DOCTOR_REPORT_PAYLOAD_CASES = [
    pytest.param(
        DiagnosticsReport.model_validate(
            {
                **FAILED_HEADER,
                "workspace_root": ROOT,
                "checks": [WARNING_CHECK, FAILED_CHECK],
                "duration_seconds": 0.0124,
            }
        ),
        {
            "ok": False,
            "error_code": "DOCTOR_CHECKS_FAILED",
            "errors": ["Configuration file not found at '/workspace/my-repo/.dovo/config.json'."],
            "warnings": ["Agent provider 'copilot' has no model configured."],
            "remediations": [
                {"message": CONFIG_NOT_FOUND_MESSAGE, "command": "dovo init"},
                {"message": NO_MODEL_MESSAGE, "command": 'dovo config set agents.copilot.model "<model>"'},
            ],
            "diagnostics": [],
            "workspace_root": "/workspace/my-repo",
            "duration_seconds": 0.0124,
            "checks": [
                {
                    "check_id": "agent.setup",
                    "name": "Agent Setup Check",
                    "category": "agent",
                    "status": "warning",
                    "message": "Agent provider 'copilot' has no model configured.",
                    "details": {"provider": "copilot"},
                    "duration_seconds": 0.0005,
                    "error_code": "DOCTOR_AGENT_MODEL_MISSING",
                    "errors": [],
                    "warnings": ["Agent provider 'copilot' has no model configured."],
                    "remediations": [
                        {"message": NO_MODEL_MESSAGE, "command": 'dovo config set agents.copilot.model "<model>"'}
                    ],
                    "diagnostics": [],
                },
                {
                    "check_id": "config.schema",
                    "name": "Config Schema Check",
                    "category": "config",
                    "status": "failed",
                    "message": "Configuration file not found at '/workspace/my-repo/.dovo/config.json'.",
                    "details": {},
                    "duration_seconds": 0.0003,
                    "error_code": "CONFIG_NOT_FOUND",
                    "errors": ["Configuration file not found at '/workspace/my-repo/.dovo/config.json'."],
                    "warnings": [],
                    "remediations": [{"message": CONFIG_NOT_FOUND_MESSAGE, "command": "dovo init"}],
                    "diagnostics": [],
                },
            ],
        },
        id="warning_and_failed",
    ),
    pytest.param(
        EMPTY_CHECKS.data,
        {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "workspace_root": "/workspace/my-repo",
            "duration_seconds": 0.0,
            "checks": [],
        },
        id="empty_checks",
    ),
]


class DoctorReportFormatterTests:
    """Presentation contract tests for DoctorReportFormatter."""

    @pytest.mark.parametrize("case", DOCTOR_REPORT_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[DiagnosticsReport, DoctorReportView]) -> None:
        assert_transform_derives_expected_view(DoctorReportFormatter, case.data, case.view)

    @pytest.mark.parametrize(("report", "expected_payload"), DOCTOR_REPORT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self, report: DiagnosticsReport, expected_payload: dict[str, object]
    ) -> None:
        assert_json_payload_matches_published_shape(DoctorReportFormatter, report, expected_payload)

    def test_json_payload_starts_with_the_result_header(self) -> None:
        payload = DoctorReportFormatter().to_json_serializable(MIXED_STATUS.data)

        assert list(payload)[:6] == ["ok", "error_code", "errors", "warnings", "remediations", "diagnostics"]
        assert "status" not in payload

    @pytest.mark.parametrize("case", DOCTOR_REPORT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[DiagnosticsReport, DoctorReportView]) -> None:
        """Render every non-null view value: check ids, upper-cased statuses, summary counts and the Fix: block."""
        assert_rich_render_shows_every_view_value(DoctorReportFormatter, case.data, case.render_expectations)
