"""Contract tests for agent step wiring, assertions, and retry in StepExecution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.constants import SESSION_LOG_FILENAME
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.core.agents import (
    AgentProgress,
    AgentProgressKind,
    AgentResponse,
    AgentResponseStatus,
    ResolvedAgentSettings,
    default_tool_policy,
)
from dovo.core.catalog.definitions import StepAssert, StepDefinition
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.agent_step import build_agent_step_runner
from dovo.engine.executors.models import (
    AgentProgressSink,
    AgentProgressUpdate,
    AgentStepRunner,
    ExecutionMetadata,
    OutputCallback,
    StepDispatchOutcome,
    StepExecutionContext,
    StepResult,
)
from dovo.engine.executors.step_executor import StepExecution
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider
from tests.harness.builders import StepBuilder
from tests.harness.catalog import write_runnable_step


def _settings() -> ResolvedAgentSettings:
    return ResolvedAgentSettings(
        provider="copilot",
        model=None,
        endpoint=None,
        temperature=0.2,
        max_tokens=4096,
        tools=default_tool_policy(),
    )


def _runner(worktree: Path) -> AgentStepRunner:
    session_tmp = worktree.parent / "session-tmp"
    session_tmp.mkdir(exist_ok=True)

    return build_agent_step_runner(
        _settings(), True, session_tmp_dir=session_tmp, main_checkout=worktree, session_log_dir=None, session_dir=None
    )


def _run_agent_step(worktree: Path, step_builder: StepBuilder) -> StepResult:
    context = StepExecutionContext(step=step_builder.build(), worktree_path=worktree, agent_runner=_runner(worktree))
    return StepExecution(context).run()


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeAgentProvider) -> None:
    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, lambda token: provider)


class AgentRunnerDispatchTests:
    def test_agent_step_dispatch_passes_execution_metadata_and_loop_iteration_to_the_runner(
        self, git_repo: Path
    ) -> None:
        received: list[ExecutionMetadata] = []

        def _runner(
            step: StepDefinition,
            worktree: Path,
            on_output: OutputCallback | None,
            on_agent_line: OutputCallback | None,
            progress: AgentProgressSink,
            metadata: ExecutionMetadata,
        ) -> StepDispatchOutcome:
            received.append(metadata)
            return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")

        step = StepBuilder.agent("plan").with_id("plan-step").build()

        StepExecution(
            StepExecutionContext(step=step, worktree_path=git_repo, agent_runner=_runner, loop_iteration=3)
        ).run()
        StepExecution(StepExecutionContext(step=step, worktree_path=git_repo, agent_runner=_runner)).run()

        assert [(metadata.step.id, metadata.step.attempt, metadata.loop_iteration) for metadata in received] == [
            ("plan-step", 1, 3),
            ("plan-step", 1, None),
        ]


class AgentStepRunnerTests:
    def test_completed_no_op_still_evaluates_assert_block(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=AgentResponseStatus.TIMEOUT)))

        result = _run_agent_step(git_repo, StepBuilder.agent("plan").with_retry(max_retries=2, backoff_ms=0))

        assert result.status == "failed"
        assert result.attempts == 2
        assert result.exit_code == 202
        assert json.loads(result.stdout)["status"] == "timeout"

    def test_continue_policy_ignores_unfixable_step_but_keeps_summary_status(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=AgentResponseStatus.UNFIXABLE)))

        result = _run_agent_step(git_repo, StepBuilder.agent("plan").with_on_failure(FailurePolicy.CONTINUE))

        assert result.status == "ignored"
        assert result.exit_code == 0
        assert json.loads(result.stdout)["status"] == "unfixable"


class StepExecutionAgentRunnerTests:
    def test_uses_step_resolving_to_agent_without_runner_raises(
        self, engine_workspace: Path, engine_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        write_runnable_step(
            engine_workspace, key="planner", definition={"id": "planner", "type": "agent", "prompt": "plan"}
        )
        context = StepExecutionContext(
            step=StepDefinition(id="s1", uses="planner"), worktree_path=tmp_path, paths=engine_paths
        )

        with pytest.raises(ValueError, match=r"Step 's1' is an agent step and requires an agent runner\."):
            StepExecution(context).run()


class AgentRunnerAttemptIdentityTests:
    def test_retries_and_resumed_attempts_get_distinct_ids_and_paths(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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


class StepExecutorRejectedPatchTests:
    def test_failed_agent_outcome_path_becomes_step_result_field(self, git_repo: Path) -> None:

        def _runner(
            step: StepDefinition,
            worktree: Path,
            on_output: OutputCallback | None,
            on_agent_line: OutputCallback | None,
            progress: AgentProgressSink,
            metadata: ExecutionMetadata,
        ) -> StepDispatchOutcome:
            return StepDispatchOutcome(
                status="failed", exit_code=203, stdout="", stderr="no", error_message="no", rejected_patch_path="P"
            )

        step = StepBuilder.agent("plan").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=git_repo, agent_runner=_runner)).run()

        assert result.status == "failed"
        assert result.rejected_patch_path == "P"


class StepExecutorAgentProgressTests:
    @staticmethod
    def _reporting_runner(received: list[OutputCallback | None]) -> AgentStepRunner:
        def _runner(
            step: StepDefinition,
            worktree: Path,
            on_output: OutputCallback | None,
            on_agent_line: OutputCallback | None,
            progress: AgentProgressSink,
            metadata: ExecutionMetadata,
        ) -> StepDispatchOutcome:
            received.append(on_agent_line)
            progress.report(AgentProgress(kind=AgentProgressKind.READ, target="x"))
            return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")

        return _runner

    def test_runner_receives_a_reporter_bound_to_the_step_identity(self, git_repo: Path, tmp_path: Path) -> None:
        streamed: list[AgentProgressUpdate] = []
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        context = StepExecutionContext(
            step=StepBuilder.agent("plan").with_id("code").build(),
            worktree_path=git_repo,
            agent_runner=self._reporting_runner([]),
            on_progress=streamed.append,
            loop_id="code-loop",
            loop_iteration=2,
            session_log_dir=log_dir,
        )

        StepExecution(context).run()

        assert streamed == [AgentProgressUpdate(kind=AgentProgressKind.READ, target="x")]
        (event,) = [
            SessionLogEvent.model_validate_json(line)
            for line in (log_dir / SESSION_LOG_FILENAME).read_text(encoding="utf-8").splitlines()
        ]
        assert (event.event, event.step_id, event.loop_id, event.iteration, event.kind, event.target) == (
            SessionLogEventType.AGENT_PROGRESS,
            "code",
            "code-loop",
            2,
            "read",
            "x",
        )

    def test_progress_works_when_attempt_logs_are_disabled(self, git_repo: Path) -> None:
        received: list[OutputCallback | None] = []
        streamed: list[AgentProgressUpdate] = []
        context = StepExecutionContext(
            step=StepBuilder.agent("plan").with_id("code").build(),
            worktree_path=git_repo,
            agent_runner=self._reporting_runner(received),
            on_progress=streamed.append,
            save_attempt_logs=False,
        )

        StepExecution(context).run()

        assert received == [None]
        assert streamed == [AgentProgressUpdate(kind=AgentProgressKind.READ, target="x")]
