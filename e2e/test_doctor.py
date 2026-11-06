"""End-to-end tests for dovo doctor health checks and failure diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.conftest import DovoRunner, hanging_git_env


@pytest.mark.e2e
@pytest.mark.fixture
class DoctorCliTests:
    """E2E tests for diagnostic health check categories and failure reporting."""

    def test_doctor_category_filter_executes_isolated_category_checks(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        result = run_dovo(["doctor", "--category", "git", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        checks = envelope.get("payload", {}).get("checks", [])

        assert len(checks) > 0
        for check in checks:
            assert check["category"] == "git"

    def test_doctor_hanging_git_reports_git_timeout_with_its_record(
        self, run_dovo: DovoRunner, initialized_project: Path, tmp_path: Path
    ) -> None:
        """Exit 1 with the git.repo check failed under DOCTOR_GIT_TIMEOUT, not as a missing repository, and one diagnostics entry whose log file exists."""
        result = run_dovo(
            ["doctor", "--category", "git", "--format", "json"],
            cwd=initialized_project,
            env=hanging_git_env(tmp_path / "slow-git"),
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        git_check = next(check for check in payload["checks"] if check["check_id"] == "git.repo")
        assert git_check["error_code"] == "DOCTOR_GIT_TIMEOUT"
        assert "Git timed out after 1s" in git_check["message"]
        assert len(payload["diagnostics"]) == 1
        assert Path(payload["diagnostics"][0]["log_path"]).is_file()

    def test_doctor_corrupted_config_reports_failure_and_exits_one(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        config_path = initialized_project / ".dovo" / "config.json"
        config_path.write_text("corrupted json { invalid", encoding="utf-8")

        result = run_dovo(["doctor", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 1
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert payload.get("ok") is False

        checks = payload.get("checks", [])
        config_check = next((c for c in checks if c["check_id"] == "config.schema"), None)
        assert config_check is not None
        assert config_check["status"] == "failed"
        assert config_check["error_code"] == "CONFIG_JSON_INVALID"
        assert config_check["remediations"] == [
            {"message": "Repair JSON syntax, or restore from backup", "command": None}
        ]
