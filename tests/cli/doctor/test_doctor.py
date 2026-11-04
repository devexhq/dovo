"""Single-tier CLI integration tests for dovo doctor."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.agents.registry import PROVIDERS
from dovo.core.config.models import DEFAULT_AGENT
from dovo.core.diagnostics import CheckStatus, DiagnosticsReport
from dovo.core.diagnostics.checks.git_repo import GitRepoCheck
from dovo.core.git import git_command_error
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.exit_codes import OUTPUT_FORMATS
from tests.harness.fakes import FakeGitRunner, use_fake_git
from tests.harness.workspace_paths import initialized_workspace_paths


def _leave_healthy(_monkeypatch: pytest.MonkeyPatch) -> None:
    return None


def _hide_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dovo.core.diagnostics.checks.env_binaries.shutil.which", lambda _name: None)


def _fail_git_repo_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = git_command_error(["git", "rev-parse", "--is-inside-work-tree"], 128, "", "fatal: not a git repository")
    use_fake_git(monkeypatch, FakeGitRunner({("rev-parse", "--is-inside-work-tree"): failure}))


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
        assert "Fix:" in result.stdout
        assert "- Run 'git init'" in result.stdout
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
        duration_seconds = payload.pop("duration_seconds")
        assert isinstance(duration_seconds, float)
        assert duration_seconds >= 0.0

        check_payload = payload["checks"][0]
        check_duration_seconds = check_payload.pop("duration_seconds")
        assert isinstance(check_duration_seconds, float)
        assert check_duration_seconds >= 0.0

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
                    "diagnostics": [],
                }
            ],
            "diagnostics": [],
        }

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("arrange", "category", "check_status", "exit_code", "error_code"),
        [
            pytest.param(_leave_healthy, "git", CheckStatus.OK, 0, None, id="ok_check"),
            pytest.param(_hide_binaries, "environment", CheckStatus.WARNING, 0, None, id="warning_check"),
            pytest.param(
                _fail_git_repo_probe, "git", CheckStatus.FAILED, 1, "DOCTOR_GIT_REPO_NOT_FOUND", id="failed_check"
            ),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        dispatch_spy: list[object],
        monkeypatch: pytest.MonkeyPatch,
        output_format: str,
        arrange: Callable[[pytest.MonkeyPatch], None],
        category: str,
        check_status: CheckStatus,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        """Exit 0 for an ok or warning check and 1 for a failed one, read from the dispatched report since error_code is not in the payload."""
        arrange(monkeypatch)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "doctor", "--category", category, "--format", output_format]
        )

        assert result.exit_code == exit_code, result.output
        report = dispatch_spy[0]
        assert isinstance(report, DiagnosticsReport)
        assert [check.status for check in report.checks] == [check_status]
        assert report.error_code == error_code

    def test_doctor_cli_json_crashed_check_reports_diagnostics(
        self, cli_runner: CliRunner, cli_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 when GitRepoCheck.execute raises, with the crashed check's message and errors free of the line and its record in the check's and the top-level diagnostics."""

        def _crash(_self: GitRepoCheck, _context: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(GitRepoCheck, "execute", _crash)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "doctor", "--category", "git", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        check_payload = payload["checks"][0]
        record_json = {"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}
        assert check_payload["error_code"] == "DOCTOR_CHECK_CRASHED"
        assert check_payload["message"] == "Unhandled exception during check execution: boom"
        assert check_payload["errors"] == ["Unhandled exception in git.repo: boom"]
        assert check_payload["diagnostics"] == [record_json]
        assert payload["diagnostics"] == [record_json]
