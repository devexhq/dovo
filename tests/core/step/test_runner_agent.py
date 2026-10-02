"""Contract tests for agent step provider selection in StepExecution."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import StepBuilder
from worktree.core.agents import BaseAgentProvider, LocalAgentAdapter
from worktree.core.agents.models import ResolvedAgentSettings
from worktree.core.step.models import StepExecutionContext, StepResult
from worktree.core.step.runner import StepExecution


def _settings(provider: str) -> ResolvedAgentSettings:
    return ResolvedAgentSettings(provider=provider, model=None, endpoint=None, temperature=0.2, max_tokens=4096)


def _run_agent_step(tmp_path: Path, agent: ResolvedAgentSettings | None) -> StepResult:
    step = StepBuilder.agent("fix it").build()
    return StepExecution(StepExecutionContext(step=step, sandbox_path=tmp_path, agent=agent)).run()


def _record_requested_providers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    requested: list[str] = []

    def _record(provider: str) -> BaseAgentProvider:
        requested.append(provider)
        return LocalAgentAdapter()

    monkeypatch.setattr("worktree.core.step.runner.get_agent_adapter", _record)
    return requested


class AgentStepRunnerTests:
    def test_agent_none_defaults_to_local_provider(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] StepExecution.run: an agent step with StepExecutionContext.agent None completes with the 'local' adapter resolved."""
        requested = _record_requested_providers(monkeypatch)

        result = _run_agent_step(tmp_path, None)

        assert result.status == "completed"
        assert requested == ["local"]

    def test_agent_provider_selects_adapter_from_resolved_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] StepExecution.run: an agent step with agent.provider 'ollama' calls get_agent_adapter exactly once with 'ollama' and completes."""
        requested = _record_requested_providers(monkeypatch)

        result = _run_agent_step(tmp_path, _settings("ollama"))

        assert result.status == "completed"
        assert requested == ["ollama"]

    def test_unregistered_provider_fails_step_with_classified_error(self, tmp_path: Path) -> None:
        """[tier-1/unit] StepExecution.run: an agent step with agent.provider 'openai' fails with an error_message naming AGENT_PROVIDER_UNSUPPORTED."""
        result = _run_agent_step(tmp_path, _settings("openai"))

        assert result.status == "failed"
        assert "Unsupported agent provider 'openai' (AGENT_PROVIDER_UNSUPPORTED)" in (result.error_message or "")
