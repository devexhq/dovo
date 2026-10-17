"""Tier 2 presentation contracts for DoctorReportFormatter."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.cli.ui.formatters.doctor import DoctorCheckView, DoctorReportFormatter, DoctorReportView
from dovo.core.diagnostics import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    DiagnosticsReport,
    Remediation,
    RemediationType,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

ROOT = Path("/workspace/my-repo")

OK_CHECK = DiagnosticCheckResult(
    check_id="git.repo",
    name="Git Repository Check",
    category=CheckCategory.GIT,
    status=CheckStatus.OK,
    message="Git repository detected at '/workspace/my-repo' on branch 'main'.",
    details={"root": "/workspace/my-repo", "branch": "main"},
    duration_ms=2.1,
    error_code=None,
    errors=[],
    warnings=[],
    remediations=[],
)

WARNING_CHECK = DiagnosticCheckResult(
    check_id="agent.setup",
    name="Agent Setup Check",
    category=CheckCategory.AGENT,
    status=CheckStatus.WARNING,
    message="Agent provider 'copilot' has no model configured.",
    details={"provider": "copilot"},
    duration_ms=0.5,
    error_code="DOCTOR_AGENT_NO_MODEL",
    errors=[],
    warnings=["Agent provider 'copilot' has no model configured."],
    remediations=[
        Remediation(
            code="DOCTOR_AGENT_NO_MODEL",
            title="Configure agent model",
            action_type=RemediationType.COMMAND,
            command='dovo config set agents.copilot.model "<model>"',
            description=(
                "Set `agents.copilot.model` in `.dovo/config.json` to a model supported by that provider, "
                "e.g. `dovo config set agents.copilot.model <model-name>`."
            ),
            doc_path="docs/cli/config.md",
            is_automated=False,
        )
    ],
)

FAILED_CHECK = DiagnosticCheckResult(
    check_id="config.schema",
    name="Config Schema Check",
    category=CheckCategory.CONFIG,
    status=CheckStatus.FAILED,
    message="Configuration file not found at '/workspace/my-repo/.dovo/config.json'.",
    details={},
    duration_ms=0.3,
    error_code="DOCTOR_CONFIG_NOT_FOUND",
    errors=["Configuration file not found at '/workspace/my-repo/.dovo/config.json'."],
    warnings=[],
    remediations=[
        Remediation(
            code="DOCTOR_CONFIG_NOT_FOUND",
            title="Initialize Dovo workspace",
            action_type=RemediationType.COMMAND,
            command="dovo init",
            description="Run `dovo init` to create `.dovo/config.json` and the required workspace directory structure.",
            doc_path="docs/cli/init.md",
            is_automated=False,
        )
    ],
)

SKIPPED_CHECK = DiagnosticCheckResult(
    check_id="worktree.refs",
    name="Worktree References Check",
    category=CheckCategory.WORKTREE,
    status=CheckStatus.SKIPPED,
    message="Check 'worktree.refs' skipped by configuration.",
    details={},
    duration_ms=0.0,
    error_code=None,
    errors=[],
    warnings=[],
    remediations=[],
)


def _check_view(check: DiagnosticCheckResult) -> DoctorCheckView:
    """Build the expected DoctorCheckView for a DiagnosticCheckResult, field for field."""
    return DoctorCheckView(
        check_id=check.check_id,
        name=check.name,
        category=check.category,
        status=check.status,
        message=check.message,
        details=check.details,
        duration_ms=check.duration_ms,
        error_code=check.error_code,
        errors=check.errors,
        warnings=check.warnings,
        remediations=check.remediations,
    )


MIXED_STATUS = FormatterCase(
    data=DiagnosticsReport(
        workspace_root=ROOT,
        checks=[OK_CHECK, WARNING_CHECK, FAILED_CHECK, SKIPPED_CHECK],
        total_duration_ms=12.4,
    ),
    view=DoctorReportView(
        ok=False,
        has_warnings=True,
        workspace_root=ROOT,
        total_duration_ms=12.4,
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
        "4 checks: 1 ok, 1 warning, 1 failed (12.4ms)",
        "Configure agent model:",
        'dovo config set agents.copilot.model "<model>"',
        "Initialize Dovo workspace:",
        "dovo init",
    ],
)

EMPTY_CHECKS = FormatterCase(
    data=DiagnosticsReport(workspace_root=ROOT, checks=[], total_duration_ms=0.0),
    view=DoctorReportView(ok=True, has_warnings=False, workspace_root=ROOT, total_duration_ms=0.0, checks=[]),
    render_expectations=["0 checks: 0 ok, 0 warning, 0 failed (0.0ms)"],
)

DOCTOR_REPORT_CASES = [
    pytest.param(MIXED_STATUS, id="mixed_status"),
    pytest.param(EMPTY_CHECKS, id="empty_checks"),
]

DOCTOR_REPORT_PAYLOAD_CASES = [
    pytest.param(
        DiagnosticsReport(workspace_root=ROOT, checks=[WARNING_CHECK, FAILED_CHECK], total_duration_ms=12.4),
        {
            "ok": False,
            "has_warnings": True,
            "workspace_root": "/workspace/my-repo",
            "total_duration_ms": 12.4,
            "checks": [
                {
                    "check_id": "agent.setup",
                    "name": "Agent Setup Check",
                    "category": "agent",
                    "status": "warning",
                    "message": "Agent provider 'copilot' has no model configured.",
                    "details": {"provider": "copilot"},
                    "duration_ms": 0.5,
                    "error_code": "DOCTOR_AGENT_NO_MODEL",
                    "errors": [],
                    "warnings": ["Agent provider 'copilot' has no model configured."],
                    "remediations": [
                        {
                            "code": "DOCTOR_AGENT_NO_MODEL",
                            "title": "Configure agent model",
                            "action_type": "command",
                            "command": 'dovo config set agents.copilot.model "<model>"',
                            "description": (
                                "Set `agents.copilot.model` in `.dovo/config.json` to a model supported by that provider, "
                                "e.g. `dovo config set agents.copilot.model <model-name>`."
                            ),
                            "doc_path": "docs/cli/config.md",
                            "is_automated": False,
                        }
                    ],
                },
                {
                    "check_id": "config.schema",
                    "name": "Config Schema Check",
                    "category": "config",
                    "status": "failed",
                    "message": "Configuration file not found at '/workspace/my-repo/.dovo/config.json'.",
                    "details": {},
                    "duration_ms": 0.3,
                    "error_code": "DOCTOR_CONFIG_NOT_FOUND",
                    "errors": ["Configuration file not found at '/workspace/my-repo/.dovo/config.json'."],
                    "warnings": [],
                    "remediations": [
                        {
                            "code": "DOCTOR_CONFIG_NOT_FOUND",
                            "title": "Initialize Dovo workspace",
                            "action_type": "command",
                            "command": "dovo init",
                            "description": (
                                "Run `dovo init` to create `.dovo/config.json` and the required workspace "
                                "directory structure."
                            ),
                            "doc_path": "docs/cli/init.md",
                            "is_automated": False,
                        }
                    ],
                },
            ],
        },
        id="warning_and_failed",
    ),
    pytest.param(
        EMPTY_CHECKS.data,
        {
            "ok": True,
            "has_warnings": False,
            "workspace_root": "/workspace/my-repo",
            "total_duration_ms": 0.0,
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

    @pytest.mark.parametrize("case", DOCTOR_REPORT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[DiagnosticsReport, DoctorReportView]) -> None:
        """Render every non-null view value: check ids, upper-cased statuses, summary counts and fix bullets."""
        assert_rich_render_shows_every_view_value(DoctorReportFormatter, case.data, case.render_expectations)
