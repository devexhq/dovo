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


@pytest.mark.e2e
@pytest.mark.fixture
class UsageErrorCliTests:
    """End-to-end tests that a usage error wins over an uninitialized workspace."""

    def test_usage_error_in_uninitialized_repo_exits_two_with_usage(
        self, run_dovo: DovoRunner, git_project: Path
    ) -> None:
        result = run_dovo(["worktree", "list", "--format", "nope"], cwd=git_project)

        assert result.exit_code == 2
        assert "Invalid value for '--format'" in result.stderr
        assert "Workspace is not initialized" not in result.stdout
        assert not (git_project / ".dovo" / "project.json").exists()

    def test_run_keep_discard_in_uninitialized_repo_exits_two_without_initializing(
        self, run_dovo: DovoRunner, git_project: Path
    ) -> None:
        result = run_dovo(["run", "anything", "--keep", "--discard"], cwd=git_project)

        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr
        assert not (git_project / ".dovo" / "project.json").exists()
