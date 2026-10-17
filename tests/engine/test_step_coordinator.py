"""Contract tests for per-step execution and failure-policy coordination."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.agents import AgentProgressKind, default_tool_policy
from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.worktree import WorktreeSession
from dovo.engine.executors.models import AgentProgressUpdate, StepExecutionContext, StepResult
from dovo.engine.executors.step_executor import StepExecution
from dovo.engine.failure import USER_CONTINUED_MARKER
from dovo.engine.models import FailurePromptDecision, RunContext, RunSettings, StepAction
from dovo.engine.step_coordinator import StepCoordinator
from tests.engine.conftest import AgentRunnerBuild
from tests.harness.builders import StepBuilder
from tests.harness.sessions import RecordingRunObserver, ScriptedFailurePrompter
from tests.harness.workspace_paths import initialized_workspace_paths

_AGENT_SETTINGS = ResolvedAgentSettings(
    provider="copilot",
    model="test-model",
    endpoint="http://127.0.0.1:9999",
    temperature=0.7,
    max_tokens=512,
    tools=default_tool_policy(),
)


def _run_context(tmp_path: Path, *, session_log_dir: Path | None = None) -> RunContext:
    return RunContext(
        session_id="sess-1",
        paths=initialized_workspace_paths(tmp_path),
        target_dir=tmp_path,
        session_tmp_dir=None,
        session_log_dir=session_log_dir,
        artifact_store=None,
    )


class BuildStepContextTests:
    def test_no_agent_or_inputs_returns_none(self, tmp_path: Path) -> None:
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))

        step_context = StepCoordinator(context).build_step_context()

        assert step_context is None

    def test_agent_set_without_inputs_returns_none(self, tmp_path: Path) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, agent=_AGENT_SETTINGS, paths=initialized_workspace_paths(tmp_path)
        )

        assert StepCoordinator(context).build_step_context() is None

    def test_inputs_set_returns_inputs_only_dict(self, tmp_path: Path) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, inputs={"branch": "main"}, paths=initialized_workspace_paths(tmp_path)
        )

        assert StepCoordinator(context).build_step_context() == {"inputs": {"branch": "main"}}


class StepCoordinatorAgentRunnerTests:
    def test_run_attempt_builds_runner_from_resolved_agent_identity(
        self,
        tmp_path: Path,
        captured_builds: list[AgentRunnerBuild],
        captured_contexts: list[StepExecutionContext],
    ) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, agent=_AGENT_SETTINGS, paths=initialized_workspace_paths(tmp_path)
        )
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert len(captured_builds) == 1
        assert captured_builds[0].agent is _AGENT_SETTINGS
        assert len(captured_contexts) == 1
        assert captured_contexts[0].agent_runner is not None

    @pytest.mark.parametrize(
        ("has_worktree", "expected"),
        [pytest.param(True, True, id="active-worktree"), pytest.param(False, False, id="no-worktree")],
    )
    def test_run_attempt_binds_worktree_active_from_run_context(
        self, tmp_path: Path, captured_builds: list[AgentRunnerBuild], has_worktree: bool, expected: bool
    ) -> None:
        session = WorktreeSession(
            session_id="s",
            target_branch="main",
            worktree_path=tmp_path,
            base_commit="abc",
            created_at="2026-01-01T00:00:00Z",
        )
        run_context = dataclasses.replace(_run_context(tmp_path), worktree=session if has_worktree else None)
        context = RunSettings(cwd=tmp_path, use_worktree=has_worktree, paths=initialized_workspace_paths(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(run_context, [], step, idx=1, total=1, step_context=None)

        assert [build.worktree_active for build in captured_builds] == [expected]


class StepCoordinatorRunnerWiringTests:
    def test_runner_is_built_with_run_context_session_dirs_and_main_checkout(
        self, tmp_path: Path, captured_builds: list[AgentRunnerBuild]
    ) -> None:
        paths = initialized_workspace_paths(tmp_path)
        session_tmp = tmp_path / "session-tmp"
        session_log = tmp_path / "session-log"
        run_context = dataclasses.replace(
            _run_context(tmp_path), session_tmp_dir=session_tmp, session_log_dir=session_log
        )
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=paths)
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(run_context, [], step, idx=1, total=1, step_context=None)

        assert [(b.session_tmp_dir, b.session_log_dir, b.main_checkout) for b in captured_builds] == [
            (session_tmp, session_log, paths.root_dir)
        ]

    @pytest.mark.parametrize(
        "session_id", [pytest.param("blueprint_abc", id="with-session"), pytest.param(None, id="no-session")]
    )
    def test_runner_session_dir_is_the_session_storage_directory_or_none(
        self, tmp_path: Path, captured_builds: list[AgentRunnerBuild], session_id: str | None
    ) -> None:
        paths = initialized_workspace_paths(tmp_path)
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=paths, session_id=session_id)
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert [build.session_dir for build in captured_builds] == [
            paths.session_dir(session_id) if session_id else None
        ]


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
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))
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
        self,
        tmp_path: Path,
        captured_contexts: list[StepExecutionContext],
        loop_id: str | None,
        expected: str | None,
    ) -> None:
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None, loop_iteration=2, loop_id=loop_id
        )

        assert [metadata.loop_id for metadata in captured_contexts] == [expected]


class StepCoordinatorProgressWiringTests:
    def test_progress_callback_notifies_the_observer_with_the_step_position(
        self, tmp_path: Path, captured_contexts: list[StepExecutionContext]
    ) -> None:
        observer = RecordingRunObserver()
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, observer=observer, paths=initialized_workspace_paths(tmp_path)
        )
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=2, total=5, step_context=None)

        [callback] = [metadata.on_progress for metadata in captured_contexts]
        assert callback is not None
        update = AgentProgressUpdate(kind=AgentProgressKind.READ, target="x.py")
        callback(update)
        assert ("step_progress", 2, 5, "ok", update) in observer.calls

    def test_progress_callback_is_absent_without_an_observer(
        self, tmp_path: Path, captured_contexts: list[StepExecutionContext]
    ) -> None:
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert [metadata.on_progress for metadata in captured_contexts] == [None]


class StepCoordinatorPrimitiveTests:
    def test_run_attempt_returns_completed_result_and_notifies_observer(self, tmp_path: Path) -> None:
        observer = RecordingRunObserver()
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, observer=observer, paths=initialized_workspace_paths(tmp_path)
        )
        step = StepBuilder.command("echo ok").with_id("ok").build()

        result = StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None
        )

        assert (result.status, result.attempts) == ("completed", 1)
        assert [call for call in observer.calls if call[0] != "step_output"] == [
            ("step_start", 1, 1, "ok"),
            ("step_done", 1, 1, "ok", "completed"),
        ]

    def test_run_attempt_returns_failed_result_without_applying_policy(self, tmp_path: Path) -> None:
        prompter = ScriptedFailurePrompter()
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, failure_prompter=prompter, paths=initialized_workspace_paths(tmp_path)
        )
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        result = StepCoordinator(context).run_attempt(
            _run_context(tmp_path), [], step, idx=1, total=1, step_context=None
        )

        assert result.status == "failed"
        assert prompter.prompted == []

    def test_run_step_forwards_sensitive_variables_to_step_execution_context(
        self, tmp_path: Path, captured_contexts: list[StepExecutionContext]
    ) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, sensitive_variables=("PIN",), paths=initialized_workspace_paths(tmp_path)
        )
        step = StepBuilder.command("echo ok").with_id("ok").build()

        StepCoordinator(context).run_attempt(_run_context(tmp_path), [], step, idx=1, total=1, step_context=None)

        assert [metadata.sensitive_variables for metadata in captured_contexts] == [("PIN",)]

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
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))
        step = StepBuilder.command("echo ok").with_id("ok").build()
        warnings: list[str] = []

        StepCoordinator(context).run_attempt(_run_context(tmp_path), warnings, step, idx=1, total=1, step_context=None)

        assert warnings == ["w1"]

    @pytest.mark.parametrize(
        ("ctx_kwargs", "prompter", "warning_substr"),
        [
            pytest.param({"no_tty": True}, ScriptedFailurePrompter(), "non-interactive", id="no-tty"),
            pytest.param({}, None, "no failure prompter", id="no-prompter"),
        ],
    )
    def test_prompt_decision_non_interactive_returns_abort_with_warning(
        self,
        tmp_path: Path,
        ctx_kwargs: dict[str, Any],
        prompter: ScriptedFailurePrompter | None,
        warning_substr: str,
    ) -> None:
        context = RunSettings(
            cwd=tmp_path,
            use_worktree=False,
            failure_prompter=prompter,
            **ctx_kwargs,
            paths=initialized_workspace_paths(tmp_path),
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
        prompter = ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, failure_prompter=prompter, paths=initialized_workspace_paths(tmp_path)
        )
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
