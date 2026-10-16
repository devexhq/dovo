"""Verification tests for test harness fixtures and assertion helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.harness import FakeAgentRunner, FakeAgentRunnerCall, initialized_workspace_paths
from tests.harness.poll import wait_until


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


class WaitUntilTests:
    """Unit tests for the shared wait_until poll helper."""

    def test_returns_true_once_the_predicate_holds(self) -> None:
        """Return True on the first poll where the predicate is true."""
        polls: list[int] = []

        def predicate() -> bool:
            polls.append(len(polls))
            return True

        assert wait_until(predicate) is True
        assert polls == [0]

    def test_returns_false_after_the_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return False once a patched clock passes the deadline, without real sleeping."""
        clock = iter([0.0, 0.0, 2.0, 3.0])
        sleeps: list[float] = []
        monkeypatch.setattr("tests.harness.poll.time.monotonic", lambda: next(clock))
        monkeypatch.setattr("tests.harness.poll.time.sleep", sleeps.append)

        assert wait_until(lambda: False, timeout=3.0, interval=0.05) is False
        assert sleeps == [0.05, 0.05]
