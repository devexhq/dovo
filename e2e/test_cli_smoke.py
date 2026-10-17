"""Smoke tests for the installed dovo CLI without repository fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.conftest import DovoRunner


@pytest.mark.e2e
class CliSmokeTests:
    """End-to-end smoke tests validating CLI startup, banner, help, and non-git error handling."""

    def test_version_without_repo_exits_zero(self, run_dovo: DovoRunner) -> None:
        result = run_dovo(["--version"])

        assert result.exit_code == 0
        assert "Dovo CLI" in result.stdout
        assert result.stderr == ""

    def test_help_without_repo_exits_zero(self, run_dovo: DovoRunner) -> None:
        result = run_dovo(["--help"])

        assert result.exit_code == 0
        assert "Usage: dovo" in result.stdout
        assert "Commands" in result.stdout
        assert result.stderr == ""

    def test_bare_invocation_without_repo_prints_banner_and_exits_zero(self, run_dovo: DovoRunner) -> None:
        result = run_dovo([])

        assert result.exit_code == 0
        assert "Dovo CLI" in result.stdout
        assert "Usage: dovo" in result.stdout
        assert "Commands" in result.stdout

    def test_init_in_non_git_dir_fails_and_creates_no_state(self, run_dovo: DovoRunner, clean_e2e_env: Path) -> None:
        result = run_dovo(["init"], cwd=clean_e2e_env)

        assert result.exit_code != 0
        assert "not a valid Git repository" in result.stdout or "not a valid Git repository" in result.stderr
        assert not (clean_e2e_env / ".dovo").exists()
