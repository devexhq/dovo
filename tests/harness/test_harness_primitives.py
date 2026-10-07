"""Verification tests for test harness fixtures and assertion helpers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from dovo.common.constants import REQUIRED_SUBDIRS
from tests.harness import FakeAgentRunner, FakeAgentRunnerCall, initialized_workspace_paths


class IsolatedWorkspaceFixtureTests:
    """Verification tests for isolated_workspace fixture."""

    def test_isolated_workspace_creates_dovo_structure(self, isolated_workspace: Path) -> None:
        dot_dovo = isolated_workspace / ".dovo"
        assert dot_dovo.is_dir()
        for subdir in REQUIRED_SUBDIRS:
            assert (dot_dovo / subdir).is_dir()
        assert (dot_dovo / "worktrees").is_dir()
        assert (dot_dovo / "catalog").is_dir()


class GitRepoFixtureTests:
    """Verification tests for git_repo fixture."""

    def test_git_repo_initializes_valid_git_repository_on_main(self, git_repo: Path) -> None:
        assert (git_repo / ".git").is_dir()

        branch_proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert branch_proc.stdout.strip() == "main"

        head_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        head_sha = head_proc.stdout.strip()
        assert len(head_sha) == 40

    @pytest.mark.parametrize(
        ("config_key", "expected_value"),
        [
            pytest.param("user.name", "Test User", id="name"),
            pytest.param("user.email", "test@example.com", id="email"),
        ],
    )
    def test_git_repo_configures_local_user_identity(
        self,
        git_repo: Path,
        config_key: str,
        expected_value: str,
    ) -> None:
        proc = subprocess.run(
            ["git", "config", config_key],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert proc.stdout.strip() == expected_value


class CliRunnerFixtureTests:
    """Verification tests for cli_runner fixture."""

    def test_cli_runner_sets_columns_and_no_color(self, cli_runner: CliRunner) -> None:
        app = typer.Typer()

        @app.command()
        def check_env() -> None:
            assert os.environ.get("COLUMNS") == "160"
            assert os.environ.get("NO_COLOR") == "1"

        result = cli_runner.invoke(app)
        assert result.exit_code == 0


class FakeAgentRunnerTests:
    """Verification tests for FakeAgentRunner."""

    def test_returning_records_call_and_returns_configured_result(self, tmp_path: Path) -> None:
        runner = FakeAgentRunner().returning(returncode=0, stdout=b"out", stderr=b"err")

        result = runner(
            ["gh", "-p", ""],
            cwd=tmp_path,
            env={"GH_TOKEN": "test-key"},
            input_data=b"hi",
            timeout_seconds=3,
        )

        assert result.args == ["gh", "-p", ""]
        assert result.returncode == 0
        assert result.stdout == b"out"
        assert result.stderr == b"err"
        assert runner.last_call == FakeAgentRunnerCall(
            cmd=["gh", "-p", ""],
            cwd=tmp_path,
            env={"GH_TOKEN": "test-key"},
            input_data=b"hi",
            timeout_seconds=3,
        )

    def test_raising_records_call_then_raises_configured_exception(self, tmp_path: Path) -> None:
        runner = FakeAgentRunner().raising(FileNotFoundError("gh"))

        with pytest.raises(FileNotFoundError, match="gh"):
            runner(["gh"], cwd=tmp_path, env={}, input_data=b"hi", timeout_seconds=3)

        assert runner.last_call == FakeAgentRunnerCall(
            cmd=["gh"], cwd=tmp_path, env={}, input_data=b"hi", timeout_seconds=3
        )

    def test_call_without_configuration_raises_assertion_error(self, tmp_path: Path) -> None:
        runner = FakeAgentRunner()

        with pytest.raises(AssertionError, match=r"without \.returning"):
            runner(["gh"], cwd=tmp_path, env={}, input_data=b"hi", timeout_seconds=3)

    def test_last_call_before_any_call_raises_assertion_error(self) -> None:
        runner = FakeAgentRunner()

        with pytest.raises(AssertionError, match="never called"):
            _ = runner.last_call

    def test_returning_after_raising_switches_back_to_returning(self, tmp_path: Path) -> None:
        runner = FakeAgentRunner().raising(FileNotFoundError("gh")).returning(returncode=1)

        result = runner(["gh"], cwd=tmp_path, env={}, input_data=b"hi", timeout_seconds=3)

        assert result.returncode == 1


class InitializedWorkspacePathsTests:
    """Verification tests for the initialized_workspace_paths helper."""

    def test_helper_writes_deterministic_identity_when_absent(self, tmp_path: Path) -> None:
        paths = initialized_workspace_paths(tmp_path)

        assert paths.project_id == "test-project"
        assert json.loads((tmp_path / ".dovo" / "project.json").read_text(encoding="utf-8"))["id"] == "test-project"

    def test_helper_preserves_an_existing_identity(self, tmp_path: Path) -> None:
        identity_file = tmp_path / ".dovo" / "project.json"
        identity_file.parent.mkdir()
        identity_file.write_text(
            json.dumps({"id": "other-project", "created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8"
        )

        paths = initialized_workspace_paths(tmp_path)

        assert paths.project_id == "other-project"
