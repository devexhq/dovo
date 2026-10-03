"""Tests for the Gemini CLI direct-mutation agent adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dovo.core.agents import AgentResponseStatus
from dovo.core.agents.cli_mutation import CliMutationRunRequest
from dovo.core.agents.gemini import (
    GEMINI_API_KEY_ENV,
    GeminiAgentAdapter,
    default_gemini_run,
    resolve_gemini_api_key,
)
from tests.harness import AgentRequestBuilder, FakeAgentRunner


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "test-key")


class GeminiAuthTests:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            pytest.param({GEMINI_API_KEY_ENV: "abc"}, "abc", id="key_present"),
            pytest.param({}, None, id="unset"),
        ],
    )
    def test_resolve_checks_gemini_api_key(self, env: dict[str, str], expected: str | None) -> None:
        """GEMINI_API_KEY resolves when present; an empty environment resolves to None."""
        assert resolve_gemini_api_key(env) == expected

    def test_preflight_requires_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing GEMINI_API_KEY fails preflight before any run."""
        monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
        adapter = GeminiAgentAdapter()

        resp = adapter.propose_fix(AgentRequestBuilder().with_sandbox_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): missing GEMINI_API_KEY. Fix: export GEMINI_API_KEY=..."
        ]


class GeminiRunTests:
    def test_default_run_parses_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gemini is invoked with the fixed argv plus the requested model, and its JSON stdout is parsed to text."""
        runner = FakeAgentRunner().returning(stdout=b'{"response": "pong"}')
        monkeypatch.setattr("dovo.core.agents.gemini.run_isolated_process", runner)

        outcome = default_gemini_run(
            CliMutationRunRequest(
                sandbox_path=tmp_path,
                prompt="hi",
                model="gemini-2.5-flash",
                timeout_seconds=3,
            )
        )

        assert outcome.status == "finished"
        assert outcome.result_text == "pong"
        assert outcome.error_detail is None

        call = runner.last_call
        assert call.cmd == ["gemini", "-p", "", "-o", "json", "--yolo", "-m", "gemini-2.5-flash"]
        assert call.cwd == tmp_path
        assert call.input_data == b"hi"
        assert call.timeout_seconds == 3
        assert call.env[GEMINI_API_KEY_ENV] == "test-key"

    def test_missing_gemini_binary_returns_error_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing gemini binary maps to an error outcome naming the Gemini CLI."""
        runner = FakeAgentRunner().raising(FileNotFoundError("gemini"))
        monkeypatch.setattr("dovo.core.agents.gemini.run_isolated_process", runner)

        outcome = default_gemini_run(
            CliMutationRunRequest(sandbox_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome.status == "error"
        assert outcome.result_text is None
        assert outcome.error_detail is not None and "install the Gemini CLI" in outcome.error_detail

    def test_process_timeout_returns_timeout_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A subprocess timeout maps to a timeout outcome."""
        runner = FakeAgentRunner().raising(subprocess.TimeoutExpired(cmd="gemini", timeout=3))
        monkeypatch.setattr("dovo.core.agents.gemini.run_isolated_process", runner)

        outcome = default_gemini_run(
            CliMutationRunRequest(sandbox_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome.status == "timeout"
        assert outcome.result_text is None
        assert outcome.error_detail is None
