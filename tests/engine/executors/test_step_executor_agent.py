"""Contract tests for agent step wiring, assertions, and retry in StepExecution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.agents import AgentResponse, AgentResponseStatus, ResolvedAgentSettings
from dovo.core.catalog.definitions import StepAssert
from dovo.engine.executors.agent_step import MISSING_SETTINGS_MESSAGE, build_agent_step_runner
from dovo.engine.executors.models import AgentStepRunner, StepExecutionContext, StepResult
from dovo.engine.executors.step_executor import StepExecution
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider
from tests.harness.builders import StepBuilder


def _settings() -> ResolvedAgentSettings:
    return ResolvedAgentSettings(provider="copilot", model=None, endpoint=None, temperature=0.2, max_tokens=4096)


def _runner(worktree: Path) -> AgentStepRunner:
    session_tmp = worktree.parent / "session-tmp"
    session_tmp.mkdir(exist_ok=True)

    return build_agent_step_runner(_settings(), True, session_tmp_dir=session_tmp, main_checkout=worktree)


def _run_agent_step(worktree: Path, step_builder: StepBuilder) -> StepResult:
    context = StepExecutionContext(step=step_builder.build(), worktree_path=worktree, agent_runner=_runner(worktree))
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
        assert json.loads(missing.stdout)["status"] == "no_op"
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
        """[tier-1/unit] StepExecution.run: on_failure retry max_retries=2 with a fake always answering TIMEOUT returns status 'failed', attempts 2, exit_code 202, and stdout JSON status 'timeout'."""
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=AgentResponseStatus.TIMEOUT)))

        result = _run_agent_step(git_repo, StepBuilder.agent("plan").with_retry(max_retries=2, backoff_ms=0))

        assert result.status == "failed"
        assert result.attempts == 2
        assert result.exit_code == 202
        assert json.loads(result.stdout)["status"] == "timeout"

    def test_continue_policy_ignores_unfixable_step_but_keeps_summary_status(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] StepExecution.run: on_failure continue with a fake answering UNFIXABLE returns status 'ignored', exit_code 0, and stdout JSON status 'unfixable'."""
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=AgentResponseStatus.UNFIXABLE)))

        result = _run_agent_step(git_repo, StepBuilder.agent("plan").with_on_failure(FailurePolicy.CONTINUE))

        assert result.status == "ignored"
        assert result.exit_code == 0
        assert json.loads(result.stdout)["status"] == "unfixable"


class StepExecutionAgentRunnerTests:
    def test_agent_step_without_agent_runner_fails_with_missing_settings_message(self, git_repo: Path) -> None:
        """[tier-1/unit] StepExecution.run: an agent step run from StepExecutionContext(agent_runner=None) returns status 'failed', exit_code 1, error_message == MISSING_SETTINGS_MESSAGE, stdout '' and stderr ''."""
        context = StepExecutionContext(step=StepBuilder.agent("plan").build(), worktree_path=git_repo)

        result = StepExecution(context).run()

        assert result.status == "failed"
        assert result.exit_code == 1
        assert result.error_message == MISSING_SETTINGS_MESSAGE
        assert result.stdout == ""
        assert result.stderr == ""


class AgentRunnerAttemptIdentityTests:
    def test_retries_and_resumed_attempts_get_distinct_ids_and_paths(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] StepExecution.run: three retry attempts, then a second StepExecution with the same step id and initial_attempt, pass four distinct invocation_ids and four distinct scratch_paths to the provider."""
        provider = FakeAgentProvider(AgentResponse(status=AgentResponseStatus.TIMEOUT))
        _use_provider(monkeypatch, provider)
        retried = StepBuilder.agent("plan").with_id("plan-step").with_retry(max_retries=3, backoff_ms=0).build()
        resumed = StepBuilder.agent("plan").with_id("plan-step").build()
        runner = _runner(git_repo)

        StepExecution(StepExecutionContext(step=retried, worktree_path=git_repo, agent_runner=runner)).run()
        StepExecution(
            StepExecutionContext(step=resumed, worktree_path=git_repo, agent_runner=runner, initial_attempt=1)
        ).run()

        invocations = [request.invocation for request in provider.requests]
        assert len(invocations) == 4
        assert all(invocation is not None for invocation in invocations)
        assert len({i.invocation_id for i in invocations if i is not None}) == 4
        assert len({i.scratch_path for i in invocations if i is not None}) == 4
        assert all(i.scratch_path.is_dir() for i in invocations if i is not None)


class AgentStepRedactionTests:
    def test_agent_step_masks_reflected_credential_in_result_and_stream(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] StepExecution.run: an agent step whose FakeAgentProvider returns summary "token SVC_SECRET-value" with SVC_SECRET set yields StepResult.stdout and the on_output line holding "[REDACTED:SVC_SECRET]" and not the literal."""
        monkeypatch.setenv("SVC_SECRET", "s3cr3t-value")
        _use_provider(
            monkeypatch,
            FakeAgentProvider(AgentResponse(status=AgentResponseStatus.NO_OP, summary="token s3cr3t-value")),
        )
        streamed: list[str] = []
        context = StepExecutionContext(
            step=StepBuilder.agent("plan").build(),
            worktree_path=git_repo,
            agent_runner=_runner(git_repo),
            on_output=lambda _stream, line: streamed.append(line),
        )

        result = StepExecution(context).run()

        assert "[REDACTED:SVC_SECRET]" in result.stdout
        assert "s3cr3t-value" not in result.stdout
        assert len(streamed) == 1
        assert "[REDACTED:SVC_SECRET]" in streamed[0]
        assert "s3cr3t-value" not in streamed[0]

    def test_agent_step_failure_diagnostic_is_masked_in_stderr_and_error_message(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] StepExecution.run: a PROVIDER_ERROR fake response whose errors hold a SVC_SECRET value yields StepResult.stderr and error_message with the literal absent."""
        monkeypatch.setenv("SVC_SECRET", "s3cr3t-value")
        _use_provider(
            monkeypatch,
            FakeAgentProvider(AgentResponse(status=AgentResponseStatus.PROVIDER_ERROR, errors=["denied s3cr3t-value"])),
        )

        result = _run_agent_step(git_repo, StepBuilder.agent("plan"))

        assert result.status == "failed"
        assert "[REDACTED:SVC_SECRET]" in result.stderr + (result.error_message or "")
        assert "s3cr3t-value" not in result.stderr
        assert "s3cr3t-value" not in (result.error_message or "")
