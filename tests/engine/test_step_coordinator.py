"""Contract tests for per-step execution and failure-policy coordination."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.core.agents import default_tool_policy
from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.catalog.definitions import StepDefinition
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
from dovo.engine.step_coordinator import StepCoordinator
from tests.harness.builders import StepBuilder
from tests.harness.sessions import NoOpRunObserver
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


_AGENT_SETTINGS = ResolvedAgentSettings(
    provider="copilot",
    model="test-model",
    endpoint="http://127.0.0.1:9999",
    temperature=0.7,
    max_tokens=512,
    tools=default_tool_policy(),
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
        artifact_store=None,
    )


class BuildStepContextTests:
    def test_no_agent_or_inputs_returns_none(self, tmp_path: Path) -> None:
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))

        step_context = StepCoordinator(context).build_step_context()

        assert step_context is None

    def test_agent_set_without_inputs_returns_none(self, tmp_path: Path) -> None:
        context = RunSettings(cwd=tmp_path, use_worktree=False, agent=_AGENT_SETTINGS, paths=_paths_for(tmp_path))

        assert StepCoordinator(context).build_step_context() is None

    def test_inputs_set_returns_inputs_only_dict(self, tmp_path: Path) -> None:
        context = RunSettings(cwd=tmp_path, use_worktree=False, inputs={"branch": "main"}, paths=_paths_for(tmp_path))

        assert StepCoordinator(context).build_step_context() == {"inputs": {"branch": "main"}}


class StepCoordinatorAgentRunnerTests:
    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[ResolvedAgentSettings | None, bool]], list[Any]]:
        """Record build_agent_step_runner calls and the agent_runner each StepExecutionContext receives."""
        builds: list[tuple[ResolvedAgentSettings | None, bool]] = []
        runners: list[Any] = []

        def _recording_build(
            agent: ResolvedAgentSettings | None,
            worktree_active: bool,
            *,
            session_tmp_dir: Path | None,
            main_checkout: Path,
            session_log_dir: Path | None,
            session_dir: Path | None,
        ) -> Any:
            builds.append((agent, worktree_active))
            return build_agent_step_runner(
                agent,
                worktree_active,
                session_tmp_dir=session_tmp_dir,
                main_checkout=main_checkout,
                session_log_dir=session_log_dir,
                session_dir=session_dir,
            )

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


class StepCoordinatorRunnerWiringTests:
    def test_runner_is_built_with_run_context_session_dirs_and_main_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scratch_roots: list[tuple[Path | None, Path | None, Path]] = []

        def _recording_build(
            agent: ResolvedAgentSettings | None,
            worktree_active: bool,
            *,
            session_tmp_dir: Path | None,
            main_checkout: Path,
            session_log_dir: Path | None,
            session_dir: Path | None,
        ) -> Any:
            scratch_roots.append((session_tmp_dir, session_log_dir, main_checkout))
            return build_agent_step_runner(
                agent,
                worktree_active,
                session_tmp_dir=session_tmp_dir,
                main_checkout=main_checkout,
                session_log_dir=session_log_dir,
                session_dir=session_dir,
            )

        monkeypatch.setattr("dovo.engine.step_coordinator.build_agent_step_runner", _recording_build)
        paths = _paths_for(tmp_path)
        session_tmp = tmp_path / "session-tmp"
        session_log = tmp_path / "session-log"
        run_context = dataclasses.replace(
            _run_context(tmp_path), session_tmp_dir=session_tmp, session_log_dir=session_log
        )
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=paths)
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(run_context, [], step, idx=1, total=1, step_context=None)

        assert scratch_roots == [(session_tmp, session_log, paths.root_dir)]

    @pytest.mark.parametrize(
        "session_id", [pytest.param("blueprint_abc", id="with-session"), pytest.param(None, id="no-session")]
    )
    def test_runner_session_dir_is_the_session_storage_directory_or_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str | None
    ) -> None:
        session_dirs: list[Path | None] = []

        def _recording_build(
            agent: ResolvedAgentSettings | None,
            worktree_active: bool,
            *,
            session_tmp_dir: Path | None,
            main_checkout: Path,
            session_log_dir: Path | None,
            session_dir: Path | None,
        ) -> Any:
            session_dirs.append(session_dir)
            return build_agent_step_runner(
                agent,
                worktree_active,
                session_tmp_dir=session_tmp_dir,
                main_checkout=main_checkout,
                session_log_dir=session_log_dir,
                session_dir=session_dir,
            )

        monkeypatch.setattr("dovo.engine.step_coordinator.build_agent_step_runner", _recording_build)
        paths = _paths_for(tmp_path)
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=paths, session_id=session_id)
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert session_dirs == [paths.session_dir(session_id) if session_id else None]


class StepCoordinatorLoopIterationForwardingTests:
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
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("check").build()
        run_context = _run_context(tmp_path, session_log_dir=tmp_path)

        StepCoordinator(context).run_attempt(
            run_context, [], step, idx=1, total=1, step_context=None, loop_iteration=loop_iteration
        )

        assert (tmp_path / expected_filename).exists()


class StepCoordinatorLoopIdTests:
    @pytest.mark.parametrize(
        ("loop_id", "expected"),
        [pytest.param("code-loop", "code-loop", id="loop-step"), pytest.param(None, None, id="top-level-step")],
    )
    def test_loop_step_context_carries_the_loop_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loop_id: str | None, expected: str | None
    ) -> None:
        seen: list[str | None] = []

        class _CapturingStepExecution(StepExecution):
            """StepExecution subclass recording the loop_id of the StepExecutionContext it is constructed with."""

            def __init__(self, metadata: StepExecutionContext) -> None:
                seen.append(metadata.loop_id)
                super().__init__(metadata)

        monkeypatch.setattr("dovo.engine.step_coordinator.StepExecution", _CapturingStepExecution)
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None, loop_iteration=2, loop_id=loop_id
        )

        assert seen == [expected]


class _RecordingRunObserver(NoOpRunObserver):
    """Test double implementing RunObserver, recording step lifecycle callbacks in call order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.events.append(f"start:{step.id}")

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        self.events.append(f"done:{result.step_id}")


class StepCoordinatorPrimitiveTests:
    def test_run_attempt_returns_completed_result_and_notifies_observer(self, tmp_path: Path) -> None:
        observer = _RecordingRunObserver()
        context = RunSettings(cwd=tmp_path, use_worktree=False, observer=observer, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        result = StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None
        )

        assert (result.status, result.attempts) == ("completed", 1)
        assert observer.events == ["start:ok", "done:ok"]

    def test_run_attempt_returns_failed_result_without_applying_policy(self, tmp_path: Path) -> None:
        prompter = _RefusingFailurePrompter()
        context = RunSettings(cwd=tmp_path, use_worktree=False, failure_prompter=prompter, paths=_paths_for(tmp_path))
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        result = StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None
        )

        assert result.status == "failed"
        assert prompter.calls == 0

    def test_run_step_forwards_sensitive_variables_to_step_execution_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[StepExecutionContext] = []

        class _CapturingStepExecution(StepExecution):
            def __init__(self, metadata: StepExecutionContext) -> None:
                captured.append(metadata)
                super().__init__(metadata)

        monkeypatch.setattr("dovo.engine.step_coordinator.StepExecution", _CapturingStepExecution)
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, sensitive_variables=("PIN",), paths=_paths_for(tmp_path)
        )
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert [metadata.sensitive_variables for metadata in captured] == [("PIN",)]

    def test_run_attempt_forwards_result_warnings_to_run_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        class _WarningStepExecution(StepExecution):
            def run(self) -> StepResult:
                return StepResult(
                    step_id="ok",
                    status="completed",
                    exit_code=0,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    warnings=["w1"],
                )

        monkeypatch.setattr("dovo.engine.step_coordinator.StepExecution", _WarningStepExecution)
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()
        warnings: list[str] = []

        StepCoordinator(context).run_attempt(_run_context(tmp_path), warnings, step, idx=1, total=1, step_context=None)

        assert warnings == ["w1"]

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
