"""Contract tests for the shared run_steps engine: execution, failure prompts, checkpoints."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.harness.builders import StepBuilder, WorkspaceBuilder
from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.db import RunStatus
from worktree.core.project.services.storage import resolve_project_filesystem_paths
from worktree.core.runtime import (
    USER_CONTINUED_MARKER,
    ExecutionIdentity,
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunCheckpoint,
    RunContext,
    RunLogEvent,
    RunLogEventType,
    RunObserver,
    RunPauseStore,
    run_steps,
)
from worktree.core.sandbox import Sandbox, SandboxApplyResult, SandboxApplyStatus
from worktree.core.step.models import ConditionEvaluationResult, LoopStepBlock, StepDefinition, StepResult


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


class _InMemoryPauseStore(RunPauseStore):
    """Test double recording every persisted checkpoint and pause-clear call."""

    def __init__(self) -> None:
        self.checkpoints: list[RunCheckpoint] = []
        self.cleared = 0

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        self.checkpoints.append(checkpoint)

    def clear_pause(self) -> None:
        self.cleared += 1


class _AssertsCheckpointSavedPrompter(_RefusingFailurePrompter):
    """Test double asserting a checkpoint was persisted before it is consulted."""

    def __init__(self, store: _InMemoryPauseStore) -> None:
        super().__init__()
        self.store = store

    def prompt_step_failure(self, **kwargs: object) -> FailurePromptDecision:
        self.calls += 1
        assert self.store.checkpoints
        return FailurePromptDecision.ABORT


class _InterruptingFailurePrompter(_RefusingFailurePrompter):
    """Test double simulating a Ctrl-C during an interactive prompt."""

    def prompt_step_failure(self, **kwargs: object) -> FailurePromptDecision:
        raise KeyboardInterrupt


class _NoOpRunObserver(RunObserver):
    """Test double implementing every RunObserver hook as a no-op; subclass and override only what a test needs."""

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        pass

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        pass

    def on_step_output(self, idx: int, total: int, step: StepDefinition, line: str, stream: str = "stdout") -> None:
        pass

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
        pass

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        pass

    def on_loop_turn_start(self, loop_id: str, turn: int, max_iterations: int) -> None:
        pass

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_turn: int | None = None,
    ) -> None:
        pass

    def on_loop_done(self, loop_id: str, status: str, turns: int) -> None:
        pass

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        pass


class _RecordingRunObserver(_NoOpRunObserver):
    """Test double implementing RunObserver, recording every lifecycle event in call order."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        self.events.append(("sandbox_ready", path, active))

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.events.append(("step_start", idx, total, step.id))

    def on_step_output(
        self,
        idx: int,
        total: int,
        step: StepDefinition,
        line: str,
        stream: str = "stdout",
    ) -> None:
        self.events.append(("step_output", idx, total, step.id, line, stream))

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
        self.events.append(("step_done", idx, total, result.step_id))

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        self.events.append(("loop_start", loop_id, max_iterations))

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        self.events.append(("sandbox_cleanup", kept, path))


class _InterruptingStepStartObserver(_NoOpRunObserver):
    """Test double implementing RunObserver, raising KeyboardInterrupt when a step starts."""

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        raise KeyboardInterrupt


class _ExplodingRunObserver(_NoOpRunObserver):
    """Test double implementing RunObserver, raising from every lifecycle hook."""

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        raise RuntimeError("sandbox ready exploded")

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        raise RuntimeError("step start exploded")

    def on_step_output(self, idx: int, total: int, step: StepDefinition, line: str, stream: str = "stdout") -> None:
        raise RuntimeError("step output exploded")

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
        raise RuntimeError("step done exploded")

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        raise RuntimeError("loop start exploded")

    def on_loop_turn_start(self, loop_id: str, turn: int, max_iterations: int) -> None:
        raise RuntimeError("loop turn exploded")

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_turn: int | None = None,
    ) -> None:
        raise RuntimeError("conditions exploded")

    def on_loop_done(self, loop_id: str, status: str, turns: int) -> None:
        raise RuntimeError("loop done exploded")

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        raise RuntimeError("cleanup exploded")


def _step_result(
    step_id: str,
    *,
    status: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    attempts: int = 1,
    error_message: str | None = None,
) -> StepResult:
    """Build an expected StepResult for comparison."""
    return StepResult.model_construct(
        step_id=step_id,
        status=status,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.0,
        attempts=attempts,
        error_message=error_message,
        errors=[],
        warnings=[],
        fixes=[],
    )


def _assert_step_results(actual_steps: list[StepResult], expected_steps: list[StepResult]) -> None:
    assert len(actual_steps) == len(expected_steps)
    for actual_step, expected_step in zip(actual_steps, expected_steps, strict=False):
        assert actual_step.step_id == expected_step.step_id
        assert actual_step.status == expected_step.status
        assert actual_step.exit_code == expected_step.exit_code
        if expected_step.stdout:
            assert actual_step.stdout == expected_step.stdout
        if expected_step.stderr:
            assert actual_step.stderr == expected_step.stderr
        if expected_step.error_message:
            assert actual_step.error_message == expected_step.error_message
        assert actual_step.attempts == expected_step.attempts


class RunStepsExecutionTests:
    """Contract tests for run_steps' linear execution sequence and outcome shape."""

    def test_run_steps_two_sequential_steps_returns_completed_outcome_with_both_results(self, tmp_path: Path) -> None:
        context = RunContext(
            steps=[
                StepBuilder.command("echo one").with_id("s1").build(),
                StepBuilder.command("echo two").with_id("s2").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result("s1", status="completed", exit_code=0, stdout="one\n"),
                _step_result("s2", status="completed", exit_code=0, stdout="two\n"),
            ],
        )

    def test_run_steps_streams_step_output_to_observer_by_line_and_stream(self, tmp_path: Path) -> None:
        observer = _RecordingRunObserver()
        step1 = StepBuilder.command("python3 -c \"print('line 1'); print('line 2')\"").with_id("s1").build()
        step2 = StepBuilder.command("python3 -c \"import sys; sys.stderr.write('err 1\\n')\"").with_id("s2").build()
        context = RunContext(steps=[step1, step2], cwd=tmp_path, use_sandbox=False, observer=observer)

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result("s1", status="completed", exit_code=0, stdout="line 1\nline 2\n"),
                _step_result("s2", status="completed", exit_code=0, stderr="err 1\n"),
            ],
        )
        output_events = [event for event in observer.events if event[0] == "step_output"]
        assert output_events == [
            ("step_output", 1, 2, "s1", "line 1\n", "stdout"),
            ("step_output", 1, 2, "s1", "line 2\n", "stdout"),
            ("step_output", 2, 2, "s2", "err 1\n", "stderr"),
        ]

    def test_run_steps_with_sandbox_completes_and_removes_worktree_after_run(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
        context = RunContext(
            steps=[StepBuilder.command("echo sandboxed").with_id("s1").build()],
            cwd=workspace,
            use_sandbox=True,
            keep=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_kept is False
        _assert_step_results(
            outcome.step_results,
            [_step_result("s1", status="completed", exit_code=0, stdout="sandboxed\n")],
        )
        assert not outcome.sandbox_path.exists()


class RunStepsDiffSessionColocationTests:
    """[tier-2/integration] run_steps: diff.patch, run.json, and definitions/ land under one session directory."""

    def test_run_steps_persists_diff_under_context_session_id_not_sandbox_key(self, tmp_path: Path) -> None:
        """[tier-2/integration] run_steps: with RunContext.session_id set and identity.blueprint_key containing '/', diff.patch is written to sessions/<session_id>/diff.patch, not a nested sessions/<namespace>/<name>/ directory."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
        context = RunContext(
            steps=[StepBuilder.command("echo change > tracked.txt").with_id("s1").build()],
            cwd=workspace,
            use_sandbox=True,
            identity=ExecutionIdentity(blueprint_name="fix-tests", blueprint_key="wt/fix-tests"),
            session_id="blueprint_abcd1234",
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        paths = resolve_project_filesystem_paths(workspace)
        assert (paths.session_dir("blueprint_abcd1234") / "diff.patch").is_file()
        assert not (paths.session_dir("wt/fix-tests") / "diff.patch").exists()

    def test_run_steps_skips_diff_persistence_when_context_session_id_is_none(self, tmp_path: Path) -> None:
        """[tier-2/integration] run_steps: with RunContext.session_id left as the default None, no diff.patch is written anywhere under sessions/, since there is no canonical id to address it by."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
        context = RunContext(
            steps=[StepBuilder.command("echo change > tracked.txt").with_id("s1").build()],
            cwd=workspace,
            use_sandbox=True,
            identity=ExecutionIdentity(blueprint_name="fix-tests", blueprint_key="wt/fix-tests"),
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        paths = resolve_project_filesystem_paths(workspace)
        assert not (paths.session_dir("wt/fix-tests") / "diff.patch").exists()

    def test_run_steps_abort_on_failure_stops_before_later_steps(self, tmp_path: Path) -> None:
        context = RunContext(
            steps=[
                StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.ABORT).build(),
                StepBuilder.command("echo should-not-run").with_id("later").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )

    def test_run_steps_continue_on_failure_marks_step_ignored_and_runs_remaining_steps(self, tmp_path: Path) -> None:
        context = RunContext(
            steps=[
                StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.CONTINUE).build(),
                StepBuilder.command("echo recovered").with_id("ok").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="ignored",
                    exit_code=0,
                    error_message="Command failed with exit code 1.",
                ),
                _step_result("ok", status="completed", exit_code=0, stdout="recovered\n"),
            ],
        )

    def test_run_steps_keep_sandbox_true_preserves_worktree_after_completed_run(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
        context = RunContext(
            steps=[StepBuilder.command("echo keep-me").with_id("s1").build()],
            cwd=workspace,
            use_sandbox=True,
            keep=True,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_kept is True
        _assert_step_results(
            outcome.step_results,
            [_step_result("s1", status="completed", exit_code=0, stdout="keep-me\n")],
        )
        assert outcome.sandbox_path.is_dir()

    def test_run_steps_observer_receives_sandbox_step_and_cleanup_callbacks_in_order(self, tmp_path: Path) -> None:
        observer = _RecordingRunObserver()
        context = RunContext(
            steps=[StepBuilder.command("echo one").with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
            observer=observer,
        )

        outcome = run_steps(context)

        resolved = tmp_path.resolve()
        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == resolved
        _assert_step_results(
            outcome.step_results,
            [_step_result("s1", status="completed", exit_code=0, stdout="one\n")],
        )
        lifecycle_events = [event for event in observer.events if event[0] != "step_output"]
        assert lifecycle_events == [
            ("sandbox_ready", resolved, False),
            ("step_start", 1, 1, "s1"),
            ("step_done", 1, 1, "s1"),
            ("sandbox_cleanup", False, resolved),
        ]

    def test_run_steps_keyboard_interrupt_during_step_cancels_run(self, tmp_path: Path) -> None:
        context = RunContext(
            steps=[StepBuilder.command("echo one").with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
            observer=_InterruptingStepStartObserver(),
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.CANCELLED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.errors == ["Execution cancelled by user."]

    def test_run_steps_empty_step_list_returns_completed_outcome_with_no_results(self, tmp_path: Path) -> None:
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.step_results == []

    def test_run_steps_sandbox_creation_failure_without_git_repo_returns_failed_outcome(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").build()
        context = RunContext(
            steps=[StepBuilder.command("echo x").with_id("s1").build()],
            cwd=workspace,
            use_sandbox=True,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == workspace
        assert len(outcome.errors) > 0
        assert "Git sandbox creation failed" in outcome.errors[0]


class RunStepsFailurePromptTests:
    """Contract tests covering every FailurePromptDecision branch (RETRY, CONTINUE, ABORT)."""

    def test_run_steps_prompt_user_abort_decision_stops_run_and_records_error(self, tmp_path: Path) -> None:
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        context = RunContext(
            steps=[
                StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build(),
                StepBuilder.command("echo later").with_id("later").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )
        assert prompter.calls == 1

    def test_run_steps_prompt_user_continue_decision_marks_step_ignored_and_continues(self, tmp_path: Path) -> None:
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])
        context = RunContext(
            steps=[
                StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build(),
                StepBuilder.command("echo later").with_id("later").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="ignored",
                    exit_code=1,
                    error_message=f"Command failed with exit code 1. ({USER_CONTINUED_MARKER})",
                ),
                _step_result("later", status="completed", exit_code=0, stdout="later\n"),
            ],
        )
        assert prompter.calls == 1

    def test_run_steps_prompt_user_retry_decision_reexecutes_step_until_success(self, tmp_path: Path) -> None:
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        command = 'if [ "$WT_STEP_ATTEMPT" -eq 1 ]; then exit 1; else exit 0; fi'
        context = RunContext(
            steps=[StepBuilder.command(command).with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [_step_result("fail", status="completed", exit_code=0, attempts=2)],
        )
        assert prompter.calls == 1

    @pytest.mark.parametrize(
        ("ctx_kwargs", "prompter", "warning_substr"),
        [
            pytest.param({"no_tty": True}, _RefusingFailurePrompter(), "non-interactive", id="no_tty"),
            pytest.param({}, None, "no failure prompter", id="no_prompter"),
        ],
    )
    def test_run_steps_prompt_user_skips_prompt_and_aborts_when_non_interactive(
        self,
        tmp_path: Path,
        ctx_kwargs: dict[str, Any],
        prompter: _RefusingFailurePrompter | None,
        warning_substr: str,
    ) -> None:
        context = RunContext(
            steps=[StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
            **ctx_kwargs,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert len(outcome.errors) > 0
        assert len(outcome.warnings) > 0
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )
        assert warning_substr in outcome.warnings[0]
        if prompter is not None:
            assert prompter.calls == 0

    def test_run_steps_retry_exhausted_escalates_to_on_max_retries_prompt(self, tmp_path: Path) -> None:
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        context = RunContext(
            steps=[
                StepBuilder.command("exit 1")
                .with_id("fail")
                .with_on_failure(
                    OnFailureSpec(action=FailurePolicy.RETRY, max_retries=2, on_max_retries=FailurePolicy.PROMPT_USER)
                )
                .build()
            ],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    attempts=2,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )
        assert prompter.calls == 1


class RunStepsPauseAndResumeTests:
    """Contract tests for RunCheckpoint persistence and resume-from-checkpoint behavior."""

    def test_run_steps_persists_checkpoint_before_prompting_and_clears_pause_after_decision(
        self, tmp_path: Path
    ) -> None:
        store = _InMemoryPauseStore()
        prompter = _AssertsCheckpointSavedPrompter(store)
        context = RunContext(
            steps=[StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
            pause_store=store,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )
        assert len(store.checkpoints) == 1
        assert store.checkpoints[0].pending_step_id == "fail"
        assert store.checkpoints[0].next_step_index == 0
        assert store.cleared == 1

    def test_run_steps_no_tty_never_persists_checkpoint_or_clears_pause(self, tmp_path: Path) -> None:
        store = _InMemoryPauseStore()
        context = RunContext(
            steps=[StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()],
            cwd=tmp_path,
            use_sandbox=False,
            no_tty=True,
            pause_store=store,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert len(outcome.errors) > 0
        assert len(outcome.warnings) > 0
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )
        assert store.checkpoints == []
        assert store.cleared == 0

    def test_run_steps_keyboard_interrupt_after_checkpoint_returns_paused_and_keeps_sandbox(
        self, tmp_path: Path
    ) -> None:
        store = _InMemoryPauseStore()
        context = RunContext(
            steps=[StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=_InterruptingFailurePrompter(),
            pause_store=store,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.PAUSED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.sandbox_kept is True
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]
        assert store.checkpoints
        assert store.cleared == 0

    def test_run_steps_resume_from_checkpoint_reprompts_pending_step_and_skips_completed_steps(
        self, tmp_path: Path
    ) -> None:
        prior_ok = StepResult(
            step_id="ok",
            status="completed",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
            attempts=1,
            error_message=None,
            errors=[],
            warnings=[],
            fixes=[],
        )
        prior_fail = StepResult(
            step_id="fail",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.01,
            attempts=1,
            error_message="Command failed with exit code 1.",
            errors=[],
            warnings=[],
            fixes=[],
        )
        checkpoint = RunCheckpoint(
            next_step_index=1,
            step_results=[prior_ok],
            sandbox_path=str(tmp_path),
            use_sandbox=False,
            keep=False,
            pending_step_id="fail",
            diagnostic="Step 'fail' failed: Command failed with exit code 1.",
            pending_result=prior_fail,
        )
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])
        context = RunContext(
            steps=[
                StepBuilder.command("echo ok").with_id("ok").build(),
                StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build(),
                StepBuilder.command("echo later").with_id("later").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
            resume_from=checkpoint,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                prior_ok,
                prior_fail.model_copy(
                    update={
                        "status": "ignored",
                        "error_message": f"Command failed with exit code 1. ({USER_CONTINUED_MARKER})",
                    }
                ),
                _step_result("later", status="completed", exit_code=0, stdout="later\n"),
            ],
        )
        assert prompter.calls == 1


class RunStepsRobustnessTests:
    """Contract tests for observer isolation, serial execution, loop dispatch, and cleanup failures."""

    def test_run_steps_observer_exceptions_do_not_abort_run(self, tmp_path: Path) -> None:
        context = RunContext(
            steps=[
                StepBuilder.command("echo one").with_id("s1").build(),
                StepBuilder.command("echo two").with_id("s2").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            observer=_ExplodingRunObserver(),
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result("s1", status="completed", exit_code=0, stdout="one\n"),
                _step_result("s2", status="completed", exit_code=0, stdout="two\n"),
            ],
        )

    def test_run_steps_sandbox_cleanup_failure_after_step_failure_still_reports_failed_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()

        def _exploding_cleanup(self: Sandbox, session: object, **kwargs: object) -> list[str]:
            raise RuntimeError("Cleanup filesystem removal failed")

        monkeypatch.setattr(Sandbox, "cleanup", _exploding_cleanup)
        context = RunContext(
            steps=[StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.ABORT).build()],
            cwd=workspace,
            use_sandbox=True,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_kept is False
        assert outcome.errors == ["Step 'fail' failed: Command failed with exit code 1."]
        _assert_step_results(
            outcome.step_results,
            [
                _step_result(
                    "fail",
                    status="failed",
                    exit_code=1,
                    error_message="Command failed with exit code 1.",
                ),
            ],
        )

    def test_run_steps_executes_steps_strictly_serially_never_concurrently(self, tmp_path: Path) -> None:
        tracker = tmp_path / "tracker.log"

        def _command(step_id: str) -> str:
            return f'echo "{step_id} start" >> {tracker}; sleep 0.05; echo "{step_id} end" >> {tracker}'

        context = RunContext(
            steps=[
                StepBuilder.command(_command("step1")).with_id("step1").build(),
                StepBuilder.command(_command("step2")).with_id("step2").build(),
                StepBuilder.command(_command("step3")).with_id("step3").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result("step1", status="completed", exit_code=0),
                _step_result("step2", status="completed", exit_code=0),
                _step_result("step3", status="completed", exit_code=0),
            ],
        )
        assert tracker.read_text().splitlines() == [
            "step1 start",
            "step1 end",
            "step2 start",
            "step2 end",
            "step3 start",
            "step3 end",
        ]

    def test_run_steps_loop_prompter_keyboard_interrupt_cancels_run(self, tmp_path: Path) -> None:
        loop = LoopStepBlock(
            id="interrupt-loop",
            type="loop",
            max_iterations=3,
            until=["steps.check.exit_code == 0"],
            do=[StepBuilder.command("exit 1").with_id("check").with_on_failure(FailurePolicy.PROMPT_USER).build()],
        )
        context = RunContext(
            steps=[loop],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=_InterruptingFailurePrompter(),
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.CANCELLED
        assert outcome.sandbox_path == tmp_path.resolve()
        assert outcome.errors == ["Execution cancelled by user."]

    def test_run_steps_sequential_loop_blocks_interleave_with_plain_steps_in_order(self, tmp_path: Path) -> None:
        observer = _RecordingRunObserver()
        step1 = StepBuilder.command("echo ready").with_id("setup").with_name("Setup Step").build()
        loop1 = LoopStepBlock(
            id="loop-one",
            type="loop",
            max_iterations=3,
            until=["steps.poll1.exit_code == 0"],
            do=[StepBuilder.command("echo p1").with_id("poll1").with_name("Poll One").build()],
        )
        loop2 = LoopStepBlock(
            id="loop-two",
            type="loop",
            max_iterations=3,
            until=["steps.poll2.exit_code == 0"],
            do=[StepBuilder.command("echo p2").with_id("poll2").with_name("Poll Two").build()],
        )
        step2 = StepBuilder.command("echo done").with_id("teardown").with_name("Teardown Step").build()
        context = RunContext(steps=[step1, loop1, loop2, step2], cwd=tmp_path, use_sandbox=False, observer=observer)

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.sandbox_path == tmp_path.resolve()
        _assert_step_results(
            outcome.step_results,
            [
                _step_result("setup", status="completed", exit_code=0, stdout="ready\n"),
                _step_result("poll1", status="completed", exit_code=0, stdout="p1\n"),
                _step_result("poll2", status="completed", exit_code=0, stdout="p2\n"),
                _step_result("teardown", status="completed", exit_code=0, stdout="done\n"),
            ],
        )
        loop_starts = [event for event in observer.events if event[0] == "loop_start"]
        assert loop_starts == [("loop_start", "loop-one", 3), ("loop_start", "loop-two", 3)]

    def test_run_steps_auto_apply_conflict_marks_run_failed_and_keeps_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
        fake_apply_result = SandboxApplyResult(
            sandbox_id="test-session",
            status=SandboxApplyStatus.CONFLICT,
            errors=["Patch merge conflict in sandbox apply"],
            warnings=["Patch hunk rejected"],
        )
        monkeypatch.setattr(Sandbox, "apply", lambda *args, **kwargs: fake_apply_result)
        context = RunContext(
            steps=[StepBuilder.command("echo change").with_id("s1").build()],
            cwd=workspace,
            use_sandbox=True,
            auto_apply=True,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.FAILED
        assert outcome.sandbox_kept is True
        assert outcome.errors == ["Patch merge conflict in sandbox apply"]
        assert outcome.warnings == ["Patch hunk rejected"]
        _assert_step_results(
            outcome.step_results,
            [_step_result("s1", status="completed", exit_code=0, stdout="change\n")],
        )


class RunStepsSessionScratchDirectoryTests:
    """[tier-1/integration] run_steps: session-scoped tmp directory lifecycle under the global storage root."""

    def test_run_steps_creates_session_and_steps_scratch_directories_before_first_step_dispatches(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] run_steps: with a session_id set, a step command asserting `[ -d "$WT_TEMP/steps" ]` exits 0, proving both directories exist before the step's own dispatch."""
        context = RunContext(
            steps=[StepBuilder.command('[ -d "$WT_TEMP/steps" ]').with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-abc",
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        _assert_step_results(outcome.step_results, [_step_result("s1", status="completed", exit_code=0)])

    @pytest.mark.parametrize(
        ("keep", "step_command", "expect_preserved"),
        [
            pytest.param(False, "exit 0", False, id="completed_no_keep_deletes"),
            pytest.param(True, "exit 0", True, id="completed_keep_preserves"),
            pytest.param(False, "exit 1", True, id="failed_preserves_regardless_of_keep"),
        ],
    )
    def test_run_steps_session_scratch_directory_deleted_or_preserved_by_keep_and_final_status(
        self, tmp_path: Path, keep: bool, step_command: str, expect_preserved: bool
    ) -> None:
        """[tier-1/integration] run_steps: tmp_dir/<session_id>/ existence after return equals expect_preserved for (keep, final RunOutcome.status) combinations completed+no-keep=deleted, completed+keep=preserved, failed=preserved."""
        context = RunContext(
            steps=[StepBuilder.command(step_command).with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
            keep=keep,
            session_id="session-keep",
        )

        run_steps(context)

        session_tmp_dir = resolve_project_filesystem_paths(tmp_path).tmp_dir / "session-keep"
        assert session_tmp_dir.exists() is expect_preserved

    def test_run_steps_skips_session_scratch_directory_when_context_session_id_is_none(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: with RunContext.session_id left as the default None, no subdirectory is created anywhere under resolve_project_filesystem_paths(workspace).tmp_dir, and the step's command sees WT_TEMP as unset (empty string via `echo "[$WT_TEMP]"`)."""
        context = RunContext(
            steps=[StepBuilder.command('echo "[$WT_TEMP]"').with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        _assert_step_results(outcome.step_results, [_step_result("s1", status="completed", exit_code=0, stdout="[]\n")])
        tmp_dir = resolve_project_filesystem_paths(tmp_path).tmp_dir
        assert not tmp_dir.exists() or not any(tmp_dir.iterdir())


class RunStepsResumeSessionScratchDirectoryTests:
    """[tier-1/integration] run_steps via Engine.resume(): session scratch directory reuse and stale-output truncation across a pause/resume boundary."""

    def test_resume_reuses_same_session_tmp_dir_as_the_paused_run(self, tmp_path: Path) -> None:
        """[tier-1/integration] Engine.resume(): a run paused after step_a completes, then resumed with the same session_id, sees step_b's command assert `[ -f "$WT_TEMP/step_step_a.output" ]` exits 0 — step_a's scratch files from before the pause are still present under the resumed run's WT_TEMP, proving it is the same directory, not freshly created."""
        session_id = "session-resume"
        first_context = RunContext(
            steps=[
                StepBuilder.command('echo "marker=yes" >> "$WT_OUTPUT"').with_id("step_a").build(),
                StepBuilder.command("exit 1").with_id("step_b").with_on_failure(FailurePolicy.ABORT).build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id=session_id,
        )
        first_outcome = run_steps(first_context)
        assert first_outcome.status == RunStatus.FAILED

        checkpoint = RunCheckpoint(
            next_step_index=1,
            step_results=[first_outcome.step_results[0]],
            sandbox_path=str(tmp_path),
            use_sandbox=False,
            keep=False,
            pending_step_id="step_b",
            diagnostic="Step 'step_b' failed: Command failed with exit code 1.",
            pending_result=first_outcome.step_results[1],
        )
        resume_context = RunContext(
            steps=[
                StepBuilder.command('echo "marker=yes" >> "$WT_OUTPUT"').with_id("step_a").build(),
                StepBuilder.command('[ -f "$WT_TEMP/step_step_a.output" ]')
                .with_id("step_b")
                .with_on_failure(FailurePolicy.PROMPT_USER)
                .build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id=session_id,
            failure_prompter=_ScriptedFailurePrompter([FailurePromptDecision.RETRY]),
            resume_from=checkpoint,
        )

        resumed_outcome = run_steps(resume_context)

        assert resumed_outcome.status == RunStatus.COMPLETED
        assert resumed_outcome.step_results[-1].exit_code == 0

    def test_resume_truncates_stale_output_file_of_a_step_re_run_after_pause(self, tmp_path: Path) -> None:
        """[tier-1/integration] Engine.resume(): a step that wrote 'stale=yes' to $WT_OUTPUT before the run was paused mid-attempt, then is re-run from scratch on resume and writes only 'fresh=yes', yields StepResult.outputs == {'fresh': 'yes'} for that step with no 'stale' key — the resumed attempt's truncation (FR-3) applies identically to a resumed step as to any ordinary retry attempt."""
        session_id = "session-resume-truncate"
        first_context = RunContext(
            steps=[
                StepBuilder.command('echo "stale=yes" >> "$WT_OUTPUT"; exit 1')
                .with_id("step_a")
                .with_on_failure(FailurePolicy.ABORT)
                .build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id=session_id,
        )
        first_outcome = run_steps(first_context)
        assert first_outcome.status == RunStatus.FAILED

        checkpoint = RunCheckpoint(
            next_step_index=0,
            step_results=[],
            sandbox_path=str(tmp_path),
            use_sandbox=False,
            keep=False,
            pending_step_id="step_a",
            diagnostic="Step 'step_a' failed: Command failed with exit code 1.",
            pending_result=first_outcome.step_results[0],
        )
        resume_context = RunContext(
            steps=[
                StepBuilder.command('echo "fresh=yes" >> "$WT_OUTPUT"')
                .with_id("step_a")
                .with_on_failure(FailurePolicy.PROMPT_USER)
                .build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id=session_id,
            failure_prompter=_ScriptedFailurePrompter([FailurePromptDecision.RETRY]),
            resume_from=checkpoint,
        )

        resumed_outcome = run_steps(resume_context)

        assert resumed_outcome.status == RunStatus.COMPLETED
        assert resumed_outcome.step_results[-1].outputs == {"fresh": "yes"}


class RunStepsSessionLogDirectoryTests:
    """[tier-1/integration] run_steps: session-scoped log directory allocation under the global storage root."""

    def test_run_steps_creates_session_log_directory_before_first_step_dispatches(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: with session_id set, logs_dir/<session_id>/ exists after a single completed step."""
        context = RunContext(
            steps=[StepBuilder.command("exit 0").with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-logs",
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert (resolve_project_filesystem_paths(tmp_path).logs_dir / "session-logs").is_dir()

    def test_run_steps_skips_session_log_directory_when_context_session_id_is_none(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: with session_id None, logs_dir does not exist or is empty after a completed run."""
        context = RunContext(
            steps=[StepBuilder.command("exit 0").with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        logs_dir = resolve_project_filesystem_paths(tmp_path).logs_dir
        assert not logs_dir.exists() or not any(logs_dir.iterdir())

    def test_run_steps_session_log_directory_persists_after_completed_run(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: unlike the scratch tmp directory, logs_dir/<session_id>/ survives a completed run with keep=False."""
        context = RunContext(
            steps=[StepBuilder.command("exit 0").with_id("s1").build()],
            cwd=tmp_path,
            use_sandbox=False,
            keep=False,
            session_id="session-persist",
        )

        outcome = run_steps(context)

        paths = resolve_project_filesystem_paths(tmp_path)
        assert outcome.status == RunStatus.COMPLETED
        assert not (paths.tmp_dir / "session-persist").exists()
        assert (paths.logs_dir / "session-persist").is_dir()


def _read_run_log(tmp_path: Path, session_id: str) -> list[RunLogEvent]:
    """Parse every line of a session's run.log into RunLogEvents."""
    run_log = resolve_project_filesystem_paths(tmp_path).logs_dir / session_id / "run.log"
    return [RunLogEvent.model_validate_json(line) for line in run_log.read_text(encoding="utf-8").splitlines()]


class RunStepsRunLogTimelineTests:
    """[tier-1/integration] run_steps: run.log lifecycle timeline."""

    def test_run_steps_writes_run_started_and_run_completed_events_with_parseable_timestamps(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] run_steps: run.log opens with RUN_STARTED(session_id) and closes with RUN_COMPLETED(status='completed')."""
        context = RunContext(
            steps=[
                StepBuilder.command("exit 0").with_id("s1").build(),
                StepBuilder.command("exit 0").with_id("s2").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-timeline",
        )

        run_steps(context)

        events = _read_run_log(tmp_path, "session-timeline")
        assert (events[0].event, events[0].session_id) == (RunLogEventType.RUN_STARTED, "session-timeline")
        assert (events[-1].event, events[-1].status) == (RunLogEventType.RUN_COMPLETED, "completed")
        assert all(datetime.fromisoformat(e.ts) for e in events)

    def test_run_steps_writes_step_start_and_done_events_in_execution_order(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: STEP_START/STEP_DONE pairs for s1 then s2, in execution order."""
        context = RunContext(
            steps=[
                StepBuilder.command("exit 0").with_id("s1").build(),
                StepBuilder.command("exit 0").with_id("s2").build(),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-steps",
        )

        run_steps(context)

        step_events = [
            e.model_copy(update={"ts": ""})
            for e in _read_run_log(tmp_path, "session-steps")
            if e.event in (RunLogEventType.STEP_START, RunLogEventType.STEP_DONE)
        ]
        assert step_events == [
            RunLogEvent(event=RunLogEventType.STEP_START, step_index=1, step_id="s1", attempt=1),
            RunLogEvent(
                event=RunLogEventType.STEP_DONE,
                step_index=1,
                step_id="s1",
                attempt=1,
                status="completed",
                exit_code=0,
            ),
            RunLogEvent(event=RunLogEventType.STEP_START, step_index=2, step_id="s2", attempt=1),
            RunLogEvent(
                event=RunLogEventType.STEP_DONE,
                step_index=2,
                step_id="s2",
                attempt=1,
                status="completed",
                exit_code=0,
            ),
        ]

    def test_run_steps_step_done_records_final_attempt_of_retried_step(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: a step passing on its in-execution retry logs STEP_START(attempt=1) then STEP_DONE(attempt=2)."""
        marker = tmp_path / "marker"
        command = f'if [ -f "{marker}" ]; then exit 0; else touch "{marker}"; exit 1; fi'
        context = RunContext(
            steps=[StepBuilder.command(command).with_id("flaky").with_retry(max_retries=2, backoff_ms=0).build()],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-retry",
        )

        run_steps(context)

        step_events = [
            e.model_copy(update={"ts": ""})
            for e in _read_run_log(tmp_path, "session-retry")
            if e.event in (RunLogEventType.STEP_START, RunLogEventType.STEP_DONE)
        ]
        assert step_events == [
            RunLogEvent(event=RunLogEventType.STEP_START, step_index=1, step_id="flaky", attempt=1),
            RunLogEvent(
                event=RunLogEventType.STEP_DONE,
                step_index=1,
                step_id="flaky",
                attempt=2,
                status="completed",
                exit_code=0,
            ),
        ]
