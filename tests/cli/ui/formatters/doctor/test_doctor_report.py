"""Tier 2 presentation contracts for DoctorReportFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.doctor import DoctorCheckView, DoctorReportFormatter, DoctorReportView
from dovo.core.doctor import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheckResult,
    DoctorReport,
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
    message="Agent provider 'local' has no model configured.",
    details={"provider": "local"},
    duration_ms=0.5,
    error_code="DOCTOR_AGENT_NO_MODEL",
    errors=[],
    warnings=["Agent provider 'local' has no model configured."],
    remediations=[
        Remediation(
            code="DOCTOR_AGENT_NO_MODEL",
            title="Configure agent model",
            action_type=RemediationType.COMMAND,
            command='dovo config set agent.model "<model>"',
            description=(
                "Set `agent.model` in `.dovo/config.json` to a model supported by the configured provider, "
                "e.g. `dovo config set agent.model <model-name>`."
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
    check_id="sandbox.refs",
    name="Sandbox References Check",
    category=CheckCategory.SANDBOX,
    status=CheckStatus.SKIPPED,
    message="Check 'sandbox.refs' skipped by configuration.",
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
    data=DoctorReport(
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
        "sandbox.refs",
        "SKIPPED",
        "4 checks: 1 ok, 1 warning, 1 failed (12.4ms)",
        "Configure agent model:",
        'dovo config set agent.model "<model>"',
        "Initialize Dovo workspace:",
        "dovo init",
    ],
)

EMPTY_CHECKS = FormatterCase(
    data=DoctorReport(workspace_root=ROOT, checks=[], total_duration_ms=0.0),
    view=DoctorReportView(ok=True, has_warnings=False, workspace_root=ROOT, total_duration_ms=0.0, checks=[]),
    render_expectations=["0 checks: 0 ok, 0 warning, 0 failed (0.0ms)"],
)

DOCTOR_REPORT_CASES = [
    pytest.param(MIXED_STATUS, id="mixed_status"),
    pytest.param(EMPTY_CHECKS, id="empty_checks"),
]

DOCTOR_REPORT_PAYLOAD_CASES = [
    pytest.param(
        MIXED_STATUS,
        {
            "ok": False,
            "has_warnings": True,
            "workspace_root": "/workspace/my-repo",
            "total_duration_ms": 12.4,
            "checks": [
                {
                    "check_id": "git.repo",
                    "name": "Git Repository Check",
                    "category": "git",
                    "status": "ok",
                    "message": "Git repository detected at '/workspace/my-repo' on branch 'main'.",
                    "details": {"root": "/workspace/my-repo", "branch": "main"},
                    "duration_ms": 2.1,
                    "error_code": None,
                    "errors": [],
                    "warnings": [],
                    "remediations": [],
                },
                {
                    "check_id": "agent.setup",
                    "name": "Agent Setup Check",
                    "category": "agent",
                    "status": "warning",
                    "message": "Agent provider 'local' has no model configured.",
                    "details": {"provider": "local"},
                    "duration_ms": 0.5,
                    "error_code": "DOCTOR_AGENT_NO_MODEL",
                    "errors": [],
                    "warnings": ["Agent provider 'local' has no model configured."],
                    "remediations": [
                        {
                            "code": "DOCTOR_AGENT_NO_MODEL",
                            "title": "Configure agent model",
                            "action_type": "command",
                            "command": 'dovo config set agent.model "<model>"',
                            "description": (
                                "Set `agent.model` in `.dovo/config.json` to a model supported by the "
                                "configured provider, e.g. `dovo config set agent.model <model-name>`."
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
                {
                    "check_id": "sandbox.refs",
                    "name": "Sandbox References Check",
                    "category": "sandbox",
                    "status": "skipped",
                    "message": "Check 'sandbox.refs' skipped by configuration.",
                    "details": {},
                    "duration_ms": 0.0,
                    "error_code": None,
                    "errors": [],
                    "warnings": [],
                    "remediations": [],
                },
            ],
        },
        id="mixed_status",
    ),
    pytest.param(
        EMPTY_CHECKS,
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
    def test_transform_derives_expected_view(self, case: FormatterCase[DoctorReport, DoctorReportView]) -> None:
        """[tier-2/unit] DoctorReportFormatter.transform: derives the exact DoctorReportView for each pinned DoctorReport case (mixed-status and empty-checks)."""
        assert_transform_derives_expected_view(DoctorReportFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), DOCTOR_REPORT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self, case: FormatterCase[DoctorReport, DoctorReportView], expected_payload: dict[str, Any]
    ) -> None:
        """[tier-2/unit] DoctorReportFormatter.to_json_serializable: matches the exact published wire-format literal dict for each pinned case."""
        assert_json_payload_matches_published_shape(DoctorReportFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", DOCTOR_REPORT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[DoctorReport, DoctorReportView]) -> None:
        """[tier-2/unit] DoctorReportFormatter.to_rich: every non-null semantic view value (check ids, upper-cased statuses, summary counts, fix bullets) reaches the rendered output."""
        assert_rich_render_shows_every_view_value(DoctorReportFormatter, case.data, case.render_expectations)
