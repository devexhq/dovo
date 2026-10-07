"""Tests for the GitHub Copilot CLI direct-mutation agent adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dovo.core.agents import AgentResponseStatus
from dovo.core.agents.cli_mutation import CliMutationOutcome, CliMutationRunRequest
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
            pytest.param({"GH_TOKEN": "abc"}, "abc", id="gh-token"),
            pytest.param({"GITHUB_TOKEN": "xyz"}, "xyz", id="github-token-fallback"),
            pytest.param({"GH_TOKEN": "  ", "GITHUB_TOKEN": "xyz"}, "xyz", id="blank-gh-token-falls-through"),
            pytest.param({"GH_TOKEN": "abc", "GITHUB_TOKEN": "xyz"}, "abc", id="gh-token-precedes-github-token"),
            pytest.param({}, None, id="unset"),
        ],
    )
    def test_resolve_checks_gh_token_before_github_token(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str | None
    ) -> None:
        """[tier-1/unit] resolve_copilot_token: with both variables cleared then env applied, returns the first non-blank stripped value, else None."""
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        assert resolve_copilot_token() == expected

    def test_preflight_requires_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] CopilotAgentAdapter.invoke: GH_TOKEN and GITHUB_TOKEN unset returns PROVIDER_ERROR with the canonical missing-credential error."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        adapter = CopilotAgentAdapter()

        resp = adapter.invoke(AgentRequestBuilder().with_worktree_path(tmp_path).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): missing GH_TOKEN or GITHUB_TOKEN. "
            "Fix: export GH_TOKEN=... or export GITHUB_TOKEN=..."
        ]


class CopilotRunTests:
    def test_missing_token_returns_canonical_error_without_running_gh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] default_copilot_run: GH_TOKEN and GITHUB_TOKEN unset returns the canonical error outcome, and the patched run_isolated_process is never called."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="missing GH_TOKEN or GITHUB_TOKEN. Fix: export GH_TOKEN=... or export GITHUB_TOKEN=...",
        )
        assert runner.calls == []

    def test_default_run_parses_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gh copilot is invoked with the fixed argv and its JSONL stream is parsed to text."""
        runner = FakeAgentRunner().returning(
            stdout=(
                b'{"type":"assistant.message","data":{"content":"hello"}}\n{"type":"result","data":{"exitCode":0}}\n'
            )
        )
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        outcome = default_copilot_run(
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
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
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
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
            CliMutationRunRequest(worktree_path=tmp_path, prompt="hi", model=None, timeout_seconds=3)
        )

        assert outcome.status == "timeout"
        assert outcome.result_text is None
        assert outcome.error_detail is None


class CopilotInvokeOutcomeTests:
    def test_invoke_maps_elapsed_subprocess_timeout_to_timeout_status(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: run_isolated_process raising subprocess.TimeoutExpired returns status TIMEOUT with errors[0].splitlines()[0] == "Agent timed out after 10s (provider=copilot)." and no other status."""
        runner = FakeAgentRunner().raising(subprocess.TimeoutExpired(cmd="gh", timeout=10))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.TIMEOUT
        assert resp.errors[0].splitlines()[0] == "Agent timed out after 10s (provider=copilot)."

    @pytest.mark.parametrize(
        ("runner", "expected_error"),
        [
            pytest.param(
                FakeAgentRunner().raising(FileNotFoundError("gh")),
                "Agent provider error (AGENT_PROVIDER_ERROR): gh is not installed or not on PATH: gh. "
                "Fix: install the GitHub CLI (https://cli.github.com)",
                id="gh-missing",
            ),
            pytest.param(
                FakeAgentRunner().raising(OSError("exec failed")),
                "Agent provider error (AGENT_PROVIDER_ERROR): exec failed",
                id="os-error",
            ),
            pytest.param(
                FakeAgentRunner().returning(returncode=1, stderr=b"gh failed"),
                "Agent provider error (AGENT_PROVIDER_ERROR): gh failed",
                id="non-zero-exit",
            ),
        ],
    )
    def test_invoke_maps_non_timeout_subprocess_failure_to_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeAgentRunner, expected_error: str
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: gh missing, an OSError, or a non-zero exit returns status PROVIDER_ERROR (never TIMEOUT) with errors == [expected_error]."""
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert resp.errors == [expected_error]


class _FileWritingRunner(FakeAgentRunner):
    """Runner double that adds a file to the worktree before returning, as an editing agent would."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input_data: bytes,
        timeout_seconds: float,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        (cwd / "edited.txt").write_text("edited\n", encoding="utf-8")
        return super().__call__(cmd, cwd=cwd, env=env, input_data=input_data, timeout_seconds=timeout_seconds, **kwargs)


def _assistant_stream(text: str) -> bytes:
    return (
        json.dumps({"type": "assistant.message", "data": {"content": text}}).encode()
        + b'\n{"type":"result","data":{"exitCode":0}}\n'
    )


class CopilotRedactionTests:
    def test_invoke_masks_token_reflected_by_gh_stderr(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: GH_TOKEN="gho_"+36 chars with FakeAgentRunner returning returncode=1 and stderr containing that token yields PROVIDER_ERROR whose errors, joined, do not contain the token."""
        token = "gho_" + "a" * 36
        monkeypatch.setenv("GH_TOKEN", token)
        runner = FakeAgentRunner().returning(returncode=1, stderr=f"auth failed for {token}".encode())
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROVIDER_ERROR
        assert token not in "\n".join(resp.errors)
        assert "[REDACTED:GH_TOKEN]" in "\n".join(resp.errors)

    def test_invoke_masks_both_copilot_credential_alternatives(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: GH_TOKEN="short1" and GITHUB_TOKEN="short2" with the runner reflecting both in result text yield a response whose raw_text has neither."""
        monkeypatch.setenv("GH_TOKEN", "sh1")
        monkeypatch.setenv("GITHUB_TOKEN", "sh2")
        runner = FakeAgentRunner().returning(stdout=_assistant_stream("saw sh1 and sh2"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.raw_text == "saw [REDACTED:GH_TOKEN] and [REDACTED:GITHUB_TOKEN]"

    def test_invoke_masks_reflected_token_in_a_proposed_patch_response(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] CopilotAgentAdapter.invoke: a runner that writes a file and reflects GH_TOKEN in the final message yields PROPOSED_PATCH whose raw_text is masked and whose unified_diff is unchanged."""
        token = "gho_" + "b" * 36
        monkeypatch.setenv("GH_TOKEN", token)
        runner = _FileWritingRunner().returning(stdout=_assistant_stream(f"done with {token}"))
        monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

        resp = CopilotAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert resp.status == AgentResponseStatus.PROPOSED_PATCH
        assert resp.raw_text == "done with [REDACTED:GH_TOKEN]"
        assert resp.unified_diff is not None
        assert "+edited" in resp.unified_diff
