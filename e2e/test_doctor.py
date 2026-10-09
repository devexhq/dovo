"""End-to-end tests for dovo doctor health checks and failure diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.conftest import DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class DoctorCliTests:
    """E2E tests for diagnostic health check categories and failure reporting."""

    def test_doctor_category_filter_executes_isolated_category_checks(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Restrict health checks to a specific diagnostic category.

        Given an initialized workspace
        When dovo doctor --category git --format json is executed
        Then only checks belonging to the git category are executed and included in the report
        """
        result = run_dovo(["doctor", "--category", "git", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        checks = envelope.get("payload", {}).get("checks", [])

        assert len(checks) > 0
        for check in checks:
            assert check["category"] == "git"

    def test_doctor_corrupted_config_reports_failure_and_exits_one(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Report failure diagnostics and exit code 1 for corrupted configuration.

        Given an initialized workspace where .dovo/config.json has invalid JSON syntax
        When dovo doctor --format json is executed
        Then the command exits 1, reports a failure diagnostic with DOCTOR_CONFIG_MALFORMED, and suggests repair remediation
        """
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
        assert config_check["error_code"] == "DOCTOR_CONFIG_MALFORMED"
        assert len(config_check["remediations"]) > 0
