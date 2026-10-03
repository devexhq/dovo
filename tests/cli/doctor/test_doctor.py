"""Single-tier CLI integration tests for dovo doctor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.doctor import CheckStatus, DoctorReport


class DoctorCliIntegrationTests:
    """Typer runner integration tests for dovo doctor."""

    def test_doctor_cli_healthy_workspace_exits_zero_and_dispatches_report(
        self, cli_runner: CliRunner, doctor_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo doctor: healthy git+config workspace exits 0, dispatches the exact DoctorReport, and renders the checks table."""
        result = cli_runner.invoke(app, ["-p", str(doctor_workspace), "doctor"])

        assert result.exit_code == 0
        assert "Dovo Doctor Report" in result.stdout
        assert "git.repo" in result.stdout
        assert len(dispatch_spy) == 1
        report = dispatch_spy[0]
        assert isinstance(report, DoctorReport)
        assert report.workspace_root == doctor_workspace
        assert [c.check_id for c in report.checks] == [
            "git.repo",
            "config.schema",
            "filesystem.writable",
            "worktree.refs",
            "env.binaries",
            "agent.setup",
        ]
        assert [c.status for c in report.checks] == [
            CheckStatus.OK,
            CheckStatus.OK,
            CheckStatus.OK,
            CheckStatus.OK,
            CheckStatus.OK,
            CheckStatus.WARNING,
        ]
        assert report.total_duration_ms >= 0.0

    def test_doctor_cli_category_filter_runs_only_matching_check(
        self, cli_runner: CliRunner, doctor_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo doctor --category git: dispatched DoctorReport.checks has exactly the git.repo OK check."""
        result = cli_runner.invoke(app, ["-p", str(doctor_workspace), "doctor", "--category", "git"])

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        report = dispatch_spy[0]
        assert isinstance(report, DoctorReport)
        assert report.workspace_root == doctor_workspace
        assert len(report.checks) == 1
        assert report.checks[0].check_id == "git.repo"
        assert report.checks[0].status == CheckStatus.OK

    def test_doctor_cli_invalid_category_exits_two_without_dispatch(
        self, cli_runner: CliRunner, doctor_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo doctor --category bogus: exit code 2 from Typer choice validation; dispatch_spy captures nothing."""
        result = cli_runner.invoke(app, ["-p", str(doctor_workspace), "doctor", "--category", "bogus"])

        assert result.exit_code == 2
        assert dispatch_spy == []

    def test_doctor_cli_non_git_workspace_with_category_git_exits_one(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo doctor --category git on a non-git directory: exit 1; dispatches the exact FAILED DoctorReport naming the 'git init' fix."""
        result = cli_runner.invoke(app, ["-p", str(tmp_path), "doctor", "--category", "git"])

        assert result.exit_code == 1
        assert "FAILED" in result.stdout
        assert "Initialize Git repository" in result.stdout
        assert "git init" in result.stdout
        assert len(dispatch_spy) == 1
        report = dispatch_spy[0]
        assert isinstance(report, DoctorReport)
        assert report.workspace_root == tmp_path
        assert len(report.checks) == 1
        check = report.checks[0]
        assert check.check_id == "git.repo"
        assert check.status == CheckStatus.FAILED
        assert check.error_code == "DOCTOR_GIT_NOT_REPO"
        assert len(check.remediations) == 1
        assert check.remediations[0].command == "git init"

    def test_doctor_cli_renders_json_wire_payload_for_category_git(
        self, cli_runner: CliRunner, doctor_workspace: Path
    ) -> None:
        """dovo doctor --category git --format json: envelope event_type == 'DoctorReport'; payload matches the literal FR-5 shape for the single git.repo check, excluding duration_ms/total_duration_ms which are asserted as non-negative floats."""
        result = cli_runner.invoke(
            app, ["-p", str(doctor_workspace), "doctor", "--category", "git", "--format", "json"]
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["event_type"] == "DoctorReport"

        payload = envelope["payload"]
        total_duration_ms = payload.pop("total_duration_ms")
        assert isinstance(total_duration_ms, float)
        assert total_duration_ms >= 0.0

        check_payload = payload["checks"][0]
        check_duration_ms = check_payload.pop("duration_ms")
        assert isinstance(check_duration_ms, float)
        assert check_duration_ms >= 0.0

        assert payload == {
            "ok": True,
            "has_warnings": False,
            "workspace_root": str(doctor_workspace),
            "checks": [
                {
                    "check_id": "git.repo",
                    "name": "Git Repository Check",
                    "category": "git",
                    "status": "ok",
                    "message": f"Git repository detected at '{doctor_workspace}' on branch 'main'.",
                    "details": {"root": str(doctor_workspace), "branch": "main"},
                    "error_code": None,
                    "errors": [],
                    "warnings": [],
                    "remediations": [],
                }
            ],
        }
