"""Contract tests for per-step execution and failure-policy coordination."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.models import FailurePolicy
from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition, StepType
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree import WorktreeSession
from dovo.engine.executors.agent_step import build_agent_step_runner
from dovo.engine.executors.models import StepExecutionContext, StepResult
from dovo.engine.executors.step_executor import StepExecution
from dovo.engine.failure import USER_CONTINUED_MARKER
from dovo.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunContext,
    RunSettings,
    StepAction,
)
from dovo.engine.step_coordinator import StepCoordinator, auto_publish_step_artifacts
from tests.harness.builders import StepBuilder
from tests.harness.runs import NoOpRunObserver


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


_AGENT_SETTINGS = ResolvedAgentSettings(
    provider="ollama", model="llama3.1", endpoint="http://127.0.0.1:11434", temperature=0.7, max_tokens=512
)


class _RefusingFailurePrompter(FailurePrompter):
    """Test double refusing every FailurePrompter hook by raising; subclass and override only what a test needs."""

    def __init__(self) -> None:
        self.calls = 0

    def prompt_step_failure(self, **kwargs: object) -> FailurePromptDecision:
        self.calls += 1
        raise AssertionError("prompt_step_failure should not be called")

    def prompt_loop_max_iterations(self, **kwargs: object) -> LoopPromptDecision:
        raise AssertionError("prompt_loop_max_iterations should not be called")


class _ScriptedFailurePrompter(_RefusingFailurePrompter):
    """Test double returning a scripted queue of FailurePromptDecision values."""

    def __init__(self, decisions: list[FailurePromptDecision]) -> None:
        super().__init__()
        self.decisions = list(decisions)

    def prompt_step_failure(self, **kwargs: object) -> FailurePromptDecision:
        self.calls += 1
        return self.decisions.pop(0)


def _run_context(tmp_path: Path, *, session_log_dir: Path | None = None) -> RunContext:
    return RunContext(
        session_id="sess-1",
        paths=_paths_for(tmp_path),
        target_dir=tmp_path,
        session_tmp_dir=None,
        session_log_dir=session_log_dir,
        artifacts_dir=None,
    )


class BuildStepContextTests:
    """[tier-1/unit] StepCoordinator.build_step_context: per-step execution context assembly."""

    def test_no_agent_or_inputs_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_step_context: context.agent and context.inputs both unset returns None."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))

        step_context = StepCoordinator(context).build_step_context()

        assert step_context is None

    def test_agent_set_without_inputs_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] StepCoordinator.build_step_context: RunSettings(agent=ResolvedAgentSettings(...), inputs=None) returns None."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, agent=_AGENT_SETTINGS, paths=_paths_for(tmp_path))

        assert StepCoordinator(context).build_step_context() is None

    def test_inputs_set_returns_inputs_only_dict(self, tmp_path: Path) -> None:
        """[tier-1/unit] StepCoordinator.build_step_context: RunSettings(inputs={'branch': 'main'}) returns {'inputs': {'branch': 'main'}}."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, inputs={"branch": "main"}, paths=_paths_for(tmp_path))

        assert StepCoordinator(context).build_step_context() == {"inputs": {"branch": "main"}}


class StepCoordinatorAgentRunnerTests:
    """[tier-1/unit] StepCoordinator.run_attempt: the agent runner is built from the run's agent identity and worktree state."""

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[ResolvedAgentSettings | None, bool]], list[Any]]:
        """Record build_agent_step_runner calls and the agent_runner each StepExecutionContext receives."""
        builds: list[tuple[ResolvedAgentSettings | None, bool]] = []
        runners: list[Any] = []

        def _recording_build(agent: ResolvedAgentSettings | None, worktree_active: bool) -> Any:
            builds.append((agent, worktree_active))
            return build_agent_step_runner(agent, worktree_active)

        class _CapturingStepExecution(StepExecution):
            """StepExecution subclass recording the agent_runner of the StepExecutionContext it is constructed with."""

            def __init__(self, metadata: StepExecutionContext) -> None:
                runners.append(metadata.agent_runner)
                super().__init__(metadata)

        monkeypatch.setattr("dovo.engine.step_coordinator.build_agent_step_runner", _recording_build)
        monkeypatch.setattr("dovo.engine.step_coordinator.StepExecution", _CapturingStepExecution)
        return builds, runners

    def test_run_attempt_builds_runner_from_resolved_agent_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] StepCoordinator.run_attempt: RunSettings(agent=_AGENT_SETTINGS) calls build_agent_step_runner once with the identical _AGENT_SETTINGS object and passes its return value as StepExecutionContext.agent_runner."""
        builds, runners = self._spy(monkeypatch)
        context = RunSettings(cwd=tmp_path, use_worktree=False, agent=_AGENT_SETTINGS, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert len(builds) == 1
        assert builds[0][0] is _AGENT_SETTINGS
        assert len(runners) == 1
        assert runners[0] is not None

    @pytest.mark.parametrize(
        ("has_worktree", "expected"),
        [pytest.param(True, True, id="active-worktree"), pytest.param(False, False, id="no-worktree")],
    )
    def test_run_attempt_binds_worktree_active_from_run_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_worktree: bool, expected: bool
    ) -> None:
        """[tier-1/unit] StepCoordinator.run_attempt: RunContext.worktree set to a WorktreeSession calls build_agent_step_runner with worktree_active True; None calls it with False."""
        builds, _ = self._spy(monkeypatch)
        session = WorktreeSession(
            session_id="s",
            target_branch="main",
            worktree_path=tmp_path,
            base_commit="abc",
            created_at="2026-01-01T00:00:00Z",
        )
        run_context = dataclasses.replace(_run_context(tmp_path), worktree=session if has_worktree else None)
        context = RunSettings(cwd=tmp_path, use_worktree=has_worktree, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(run_context, [], step, idx=1, total=1, step_context=None)

        assert [worktree_active for _, worktree_active in builds] == [expected]


class StepCoordinatorLoopIterationForwardingTests:
    """[tier-1/unit] StepCoordinator.run_attempt: loop_iteration forwards to StepExecutionContext unchanged for top-level and loop callers."""

    @pytest.mark.parametrize(
        ("loop_iteration", "expected_filename"),
        [
            pytest.param(2, "01_check_iter_2_attempt_1.stdout.log", id="loop_iteration_set"),
            pytest.param(None, "01_check_attempt_1.stdout.log", id="loop_iteration_none"),
        ],
    )
    def test_run_attempt_loop_iteration_forwards_to_attempt_log_filename(
        self,
        tmp_path: Path,
        loop_iteration: int | None,
        expected_filename: str,
    ) -> None:
        """[tier-1/unit] StepCoordinator.run_attempt: loop_iteration=2 with session_log_dir set writes '01_check_iter_2_attempt_1.stdout.log'; the default loop_iteration=None keeps the '_iter_' segment absent."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("check").build()
        run_context = _run_context(tmp_path, session_log_dir=tmp_path)

        StepCoordinator(context).run_attempt(
            run_context, [], step, idx=1, total=1, step_context=None, loop_iteration=loop_iteration
        )

        assert (tmp_path / expected_filename).exists()


class _RecordingRunObserver(NoOpRunObserver):
    """Test double implementing RunObserver, recording step lifecycle callbacks in call order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.events.append(f"start:{step.id}")

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        self.events.append(f"done:{result.step_id}")


class StepCoordinatorPrimitiveTests:
    """[tier-1/integration] StepCoordinator primitives RunCoordinator composes: run_attempt, prompt_decision, apply_prompt_decision."""

    def test_run_attempt_returns_completed_result_and_notifies_observer(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepCoordinator.run_attempt: a passing echo step returns a StepResult with status "completed" and attempts 1 and the observer receives on_step_start then on_step_done."""
        observer = _RecordingRunObserver()
        context = RunSettings(cwd=tmp_path, use_worktree=False, observer=observer, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        result = StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None
        )

        assert (result.status, result.attempts) == ("completed", 1)
        assert observer.events == ["start:ok", "done:ok"]

    def test_run_attempt_returns_failed_result_without_applying_policy(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepCoordinator.run_attempt: an exit-1 step under prompt_user returns a StepResult with status "failed" and the prompter is never consulted."""
        prompter = _RefusingFailurePrompter()
        context = RunSettings(cwd=tmp_path, use_worktree=False, failure_prompter=prompter, paths=_paths_for(tmp_path))
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        result = StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None
        )

        assert result.status == "failed"
        assert prompter.calls == 0

    @pytest.mark.parametrize(
        ("ctx_kwargs", "prompter", "warning_substr"),
        [
            pytest.param({"no_tty": True}, _RefusingFailurePrompter(), "non-interactive", id="no-tty"),
            pytest.param({}, None, "no failure prompter", id="no-prompter"),
        ],
    )
    def test_prompt_decision_non_interactive_returns_abort_with_warning(
        self,
        tmp_path: Path,
        ctx_kwargs: dict[str, Any],
        prompter: _RefusingFailurePrompter | None,
        warning_substr: str,
    ) -> None:
        """[tier-1/unit] StepCoordinator.prompt_decision: no_tty=True or a missing prompter returns (ABORT, "Warning: step '<id>' requested prompt_user but ... aborting.")."""
        context = RunSettings(
            cwd=tmp_path,
            use_worktree=False,
            failure_prompter=prompter,
            **ctx_kwargs,
            paths=_paths_for(tmp_path),
        )
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()
        failed = StepResult(step_id="fail", status="failed", exit_code=1, stdout="", stderr="", duration_seconds=0.0)
        coordinator = StepCoordinator(context)

        decision, warning = coordinator.prompt_decision(step, failed)

        assert decision == FailurePromptDecision.ABORT
        assert warning is not None
        assert warning.startswith("Warning: step 'fail' requested prompt_user but")
        assert warning_substr in warning
        assert coordinator.is_interactive is False

    def test_prompt_decision_interactive_returns_prompter_decision(self, tmp_path: Path) -> None:
        """[tier-1/unit] StepCoordinator.prompt_decision: an interactive run returns the prompter's decision with no warning."""
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        context = RunSettings(cwd=tmp_path, use_worktree=False, failure_prompter=prompter, paths=_paths_for(tmp_path))
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()
        failed = StepResult(step_id="fail", status="failed", exit_code=1, stdout="", stderr="", duration_seconds=0.0)
        coordinator = StepCoordinator(context)

        assert coordinator.prompt_decision(step, failed) == (FailurePromptDecision.RETRY, None)
        assert coordinator.is_interactive is True

    @pytest.mark.parametrize(
        ("decision", "expected_action", "expected_status", "expected_error"),
        [
            pytest.param(FailurePromptDecision.RETRY, StepAction.RETRY, None, None, id="retry"),
            pytest.param(FailurePromptDecision.CONTINUE, StepAction.CONTINUE, "ignored", None, id="continue"),
            pytest.param(
                FailurePromptDecision.ABORT, StepAction.ABORT, "failed", "Step 'fail' failed: boom", id="abort"
            ),
        ],
    )
    def test_apply_prompt_decision_maps_decision_to_action(
        self,
        decision: FailurePromptDecision,
        expected_action: StepAction,
        expected_status: str | None,
        expected_error: str | None,
    ) -> None:
        """[tier-1/unit] StepCoordinator.apply_prompt_decision: RETRY returns (StepAction.RETRY, None, None), CONTINUE (StepAction.CONTINUE, an ignored result with the user-continued marker, None), ABORT (StepAction.ABORT, the failed result, "Step '<id>' failed: <detail>")."""
        failed = StepResult(
            step_id="fail",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message="boom",
        )

        action, recorded, error_message = StepCoordinator.apply_prompt_decision(decision, failed)

        assert action is expected_action
        assert (recorded.status if recorded is not None else None) == expected_status
        assert error_message == expected_error
        if decision == FailurePromptDecision.CONTINUE:
            assert recorded is not None
            assert recorded.error_message is not None
            assert recorded.error_message.endswith(f"({USER_CONTINUED_MARKER})")


class AutoPublishStepArtifactsTests:
    """[tier-1/unit] auto_publish_step_artifacts: no-session short-circuit and non-fatal publish failures."""

    def test_auto_publish_step_artifacts_no_session_returns_no_warnings(self, tmp_path: Path) -> None:
        """[tier-1/unit] auto_publish_step_artifacts: artifacts_dir=None and artifacts_db=None -> returns [] without invoking publish_artifact."""
        step = StepDefinition(
            id="s1",
            type=StepType.COMMAND,
            command="echo hi",
            artifacts=[ArtifactPublishSpec(name="coverage", path="htmlcov/**")],
        )

        warnings = auto_publish_step_artifacts(
            step,
            worktree_path=tmp_path,
            session_id="wf_abc123",
            artifacts_dir=None,
            artifacts_db=None,
        )

        assert warnings == []

    def test_auto_publish_step_artifacts_publish_failure_returns_warning_not_raise(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] auto_publish_step_artifacts: a step declaring artifacts: [{name, path}] whose glob matches nothing returns one warning string naming the artifact, and raises nothing."""
        step = StepDefinition(
            id="s1",
            type=StepType.COMMAND,
            command="echo hi",
            artifacts=[ArtifactPublishSpec(name="coverage", path="htmlcov/**")],
        )

        warnings = auto_publish_step_artifacts(
            step,
            worktree_path=tmp_path,
            session_id="wf_abc123",
            artifacts_dir=tmp_path / "artifacts",
            artifacts_db=artifacts_repository,
        )

        assert len(warnings) == 1
        assert "coverage" in warnings[0]
