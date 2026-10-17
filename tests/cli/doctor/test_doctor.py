"""Single-tier CLI integration tests for dovo doctor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import DEFAULT_AGENT
from dovo.core.diagnostics import CheckStatus, DiagnosticsReport
from tests.harness.workspace_paths import initialized_workspace_paths


class DoctorCliIntegrationTests:
    """Typer runner integration tests for dovo doctor."""

    def test_doctor_cli_healthy_workspace_exits_zero_and_dispatches_report(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        dispatch_spy: list[object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", lambda _name: "/usr/bin/tool")
        credential_envs = PROVIDERS[DEFAULT_AGENT].credential_envs
        for env_name in credential_envs:
            monkeypatch.delenv(env_name, raising=False)
        monkeypatch.setenv(credential_envs[0], "fake-token")
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "doctor"])

        assert result.exit_code == 0
        assert "Dovo Doctor Report" in result.stdout
        assert "git.repo" in result.stdout
        assert len(dispatch_spy) == 1
        report = dispatch_spy[0]
        assert isinstance(report, DiagnosticsReport)
        assert report.workspace_root == cli_workspace
        assert [c.check_id for c in report.checks] == [
            "git.repo",
            "config.schema",
            "filesystem.writable",
            "worktree.refs",
            "env.binaries",
            "agent.setup",
            "artifacts.integrity",
        ]

    def test_doctor_cli_category_filter_runs_only_matching_check(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "doctor", "--category", "git"])

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        report = dispatch_spy[0]
        assert isinstance(report, DiagnosticsReport)
        assert [c.check_id for c in report.checks] == ["git.repo"]

    def test_doctor_cli_invalid_category_exits_two_without_dispatch(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "doctor", "--category", "bogus"])

        assert result.exit_code == 2
        assert dispatch_spy == []

    def test_doctor_cli_non_git_workspace_with_category_git_exits_one(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[object]
    ) -> None:
        initialized_workspace_paths(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "doctor", "--category", "git"])

        assert result.exit_code == 1
        assert "FAILED" in result.stdout
        assert "Initialize Git repository" in result.stdout
        assert "git init" in result.stdout
        assert len(dispatch_spy) == 1
        report = dispatch_spy[0]
        assert isinstance(report, DiagnosticsReport)
        assert [c.check_id for c in report.checks] == ["git.repo"]
        assert report.checks[0].status == CheckStatus.FAILED

    def test_doctor_cli_renders_json_wire_payload_for_category_git(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Match the literal FR-5 payload for the single git.repo check, with durations as non-negative floats."""
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "doctor", "--category", "git", "--format", "json"])

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["event_type"] == "DiagnosticsReport"

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
            "workspace_root": str(cli_workspace),
            "checks": [
                {
                    "check_id": "git.repo",
                    "name": "Git Repository Check",
                    "category": "git",
                    "status": "ok",
                    "message": f"Git repository detected at '{cli_workspace}' on branch 'main'.",
                    "details": {"root": str(cli_workspace), "branch": "main"},
                    "error_code": None,
                    "errors": [],
                    "warnings": [],
                    "remediations": [],
                }
            ],
        }
