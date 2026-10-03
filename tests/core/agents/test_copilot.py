"""Tests for the GitHub Copilot CLI direct-mutation agent adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.core.agents import AgentResponseStatus
from dovo.core.agents.cli_mutation import CliMutationRunRequest
from dovo.core.agents.copilot import (
    CopilotAgentAdapter,
    default_copilot_run,
    resolve_copilot_token,
)
from tests.harness import AgentRequestBuilder, FakeAgentRunner


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "test-token")


class CopilotAuthTests:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({"GH_TOKEN": "abc"}, "abc", id="gh_token"),
            pytest.param({"GITHUB_TOKEN": "xyz"}, "xyz", id="github_token_fallback"),
            pytest.param({}, None, id="unset"),
        ],
    )
    def test_resolve_checks_gh_token_before_github_token(self, env: dict[str, str], expected: str | None) -> None:
        """GH_TOKEN takes priority over GITHUB_TOKEN; an empty environment resolves to None."""
        assert resolve_copilot_token(env) == expected

    def test_preflight_requires_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing both GH_TOKEN and GITHUB_TOKEN fails preflight before any run."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        adapter = CopilotAgentAdapter()

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): missing GH_TOKEN or GITHUB_TOKEN. Fix: export GH_TOKEN=..."
        ]


class CopilotRunTests:
    def test_default_run_parses_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gh copilot is invoked with the fixed argv and its JSONL stream is parsed to text."""
        runner = FakeAgentRunner().returning(
            stdout=(
                b'{"type":"assistant.message","data":{"content":"hello"}}\n{"type":"result","data":{"exitCode":0}}\n'
            )
        )
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(sandbox_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome.status == "finished"
        assert outcome.result_text == "hello"
        assert outcome.error_detail is None

        call = runner.last_call
        assert call.cmd == [
            "gh",
            "copilot",
            "--",
            "-p",
            "",
            "--output-format",
            "json",
            "--silent",
            "--allow-all-tools",
            "--allow-all-paths",
            "--allow-all-urls",
        ]
        assert call.cwd == tmp_path
        assert call.input_data == b"hi"
        assert call.timeout_seconds == 3
        assert call.env["GH_TOKEN"] == "test-token"

    def test_missing_gh_binary_returns_error_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing gh binary maps to an error outcome naming the GitHub CLI."""
        runner = FakeAgentRunner().raising(FileNotFoundError("gh"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(sandbox_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail == (
            "gh is not installed or not on PATH: gh. Fix: install the GitHub CLI (https://cli.github.com)"
        )

    def test_process_timeout_returns_timeout_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A subprocess timeout maps to a timeout outcome."""
        runner = FakeAgentRunner().raising(subprocess.TimeoutExpired(cmd="gh", timeout=3))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(sandbox_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome.status == "timeout"
        assert outcome.result_text is None
        assert outcome.error_detail is None
