"""Contract tests for agent step wiring, assertions, and retry in StepExecution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider
from tests.harness.builders import StepBuilder
from worktree.core.agents import AgentResponse, AgentResponseStatus, ResolvedAgentSettings
from worktree.core.step.models import StepAssert, StepExecutionContext, StepResult
from worktree.core.step.runner import StepExecution


def _settings() -> ResolvedAgentSettings:
    return ResolvedAgentSettings(provider="ollama", model=None, endpoint=None, temperature=0.2, max_tokens=4096)


def _run_agent_step(sandbox: Path, step_builder: StepBuilder) -> StepResult:
    context = StepExecutionContext(
        step=step_builder.build(), sandbox_path=sandbox, agent=_settings(), sandbox_active=True
    )
    return StepExecution(context).run()


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeAgentProvider) -> None:
    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, lambda token: provider)


class AgentStepRunnerTests:
    def test_completed_no_op_still_evaluates_assert_block(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] StepExecution.run: an agent step with a NO_OP fake completes when its output_contains matches and fails with a '[FAIL]' assertion diagnostic when it does not."""
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP, summary="ok")))

        matching = _run_agent_step(
            git_repo,
            StepBuilder.agent("plan").with_assert(StepAssert(output_contains='"status":"no_op"')),
        )
        missing = _run_agent_step(
            git_repo,
            StepBuilder.agent("plan").with_assert(StepAssert(output_contains="absent")),
        )

        assert matching.status == "completed"
        assert missing.status == "failed"
        assert "[FAIL]" in (missing.error_message or "")

    def test_retry_invokes_provider_once_per_attempt(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] StepExecution.run: on_failure retry max_retries=2 backoff_ms=0 with a fake answering PROVIDER_ERROR then NO_OP completes with attempts == 2 and exactly two recorded requests."""
        provider = FakeAgentProvider(
            AgentResponse(status=AgentResponseStatus.PROVIDER_ERROR, errors=["down"]),
            AgentResponse(status=AgentResponseStatus.NO_OP),
        )
        _use_provider(monkeypatch, provider)

        result = _run_agent_step(git_repo, StepBuilder.agent("plan").with_retry(max_retries=2, backoff_ms=0))

        assert result.status == "completed"
        assert result.attempts == 2
        assert len(provider.requests) == 2

    def test_exhausted_retries_fail_with_last_summary(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] StepExecution.run: on_failure retry max_retries=2 with a fake always answering TIMEOUT returns status 'failed', attempts 2, and stdout JSON status 'timeout'."""
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=AgentResponseStatus.TIMEOUT)))

        result = _run_agent_step(git_repo, StepBuilder.agent("plan").with_retry(max_retries=2, backoff_ms=0))

        assert result.status == "failed"
        assert result.attempts == 2
        assert json.loads(result.stdout)["status"] == "timeout"
