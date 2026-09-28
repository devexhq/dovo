"""Contract tests for per-step execution and failure-policy coordination."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.harness.builders import StepBuilder
from worktree.common.models import FailurePolicy
from worktree.core.runtime import USER_CONTINUED_MARKER
from worktree.core.runtime.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunCheckpoint,
    RunContext,
    RunPauseStore,
    StepLoopState,
)
from worktree.core.runtime.step_coordinator import StepCoordinator
from worktree.core.step.models import StepResult


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


def _state(tmp_path: Path) -> StepLoopState:
    return StepLoopState(target_dir=tmp_path, session=None)


class BuildStepContextTests:
    """[tier-1/unit] StepCoordinator.build_step_context: per-step execution context assembly."""

    def test_no_agent_or_inputs_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_step_context: context.agent and context.inputs both unset returns None."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)

        step_context = StepCoordinator(context).build_step_context()

        assert step_context is None

    def test_agent_and_inputs_set_returns_populated_dict(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_step_context: context.agent and context.inputs are surfaced under their own keys."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, agent="claude", inputs={"branch": "main"})

        step_context = StepCoordinator(context).build_step_context()

        assert step_context == {"agent": "claude", "inputs": {"branch": "main"}}


class StepCoordinatorExecuteOneStepTests:
    """[tier-1/integration] StepCoordinator.execute_one_step: run-until-terminal contract for one step."""

    def test_successful_step_returns_continue_with_result(self, tmp_path: Path) -> None:
        """[tier-1/integration] execute_one_step: a passing command returns ("continue", result, None)."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)
        step = StepBuilder.command("echo ok").with_id("s1").build()

        action, result, error_message = StepCoordinator(context).execute_one_step(
            _state(tmp_path), step, idx=1, total=1, step_index=0, step_context=None
        )

        assert action == "continue"
        assert result is not None
        assert (result.step_id, result.status, result.exit_code) == ("s1", "completed", 0)
        assert error_message is None

    def test_failed_step_default_abort_policy_returns_abort_with_error_message(self, tmp_path: Path) -> None:
        """[tier-1/integration] execute_one_step: a failing command under the default abort policy returns ("abort", result, message)."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)
        step = StepBuilder.command("exit 1").with_id("fail").build()

        action, result, error_message = StepCoordinator(context).execute_one_step(
            _state(tmp_path), step, idx=1, total=1, step_index=0, step_context=None
        )

        assert action == "abort"
        assert result is not None
        assert (result.step_id, result.status, result.exit_code) == ("fail", "failed", 1)
        assert error_message == "Step 'fail' failed: Command failed with exit code 1."

    def test_prompt_user_retry_decision_reexecutes_step_until_success(self, tmp_path: Path) -> None:
        """[tier-1/integration] execute_one_step: a RETRY decision re-runs the step, incrementing attempts, until it succeeds."""
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter)
        command = 'if [ "$WT_STEP_ATTEMPT" -eq 1 ]; then exit 1; else exit 0; fi'
        step = StepBuilder.command(command).with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        action, result, error_message = StepCoordinator(context).execute_one_step(
            _state(tmp_path), step, idx=1, total=1, step_index=0, step_context=None
        )

        assert action == "continue"
        assert result is not None
        assert (result.status, result.exit_code, result.attempts) == ("completed", 0, 2)
        assert error_message is None
        assert prompter.calls == 1

    def test_prompt_user_continue_decision_marks_step_ignored(self, tmp_path: Path) -> None:
        """[tier-1/integration] execute_one_step: a CONTINUE decision marks the failed result "ignored" with the continued marker and returns action "continue"."""
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter)
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        action, result, error_message = StepCoordinator(context).execute_one_step(
            _state(tmp_path), step, idx=1, total=1, step_index=0, step_context=None
        )

        assert action == "continue"
        assert result is not None
        assert result.status == "ignored"
        assert result.error_message == f"Command failed with exit code 1. ({USER_CONTINUED_MARKER})"
        assert error_message is None

    def test_prompt_user_abort_decision_returns_abort(self, tmp_path: Path) -> None:
        """[tier-1/integration] execute_one_step: an ABORT decision returns ("abort", result, message)."""
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter)
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        action, _, error_message = StepCoordinator(context).execute_one_step(
            _state(tmp_path), step, idx=1, total=1, step_index=0, step_context=None
        )

        assert action == "abort"
        assert error_message == "Step 'fail' failed: Command failed with exit code 1."
        assert prompter.calls == 1

    @pytest.mark.parametrize(
        ("ctx_kwargs", "prompter", "warning_substr"),
        [
            pytest.param({"no_tty": True}, _RefusingFailurePrompter(), "non-interactive", id="no_tty"),
            pytest.param({}, None, "no failure prompter", id="no_prompter"),
        ],
    )
    def test_prompt_user_skips_prompt_and_aborts_when_non_interactive(
        self,
        tmp_path: Path,
        ctx_kwargs: dict[str, Any],
        prompter: _RefusingFailurePrompter | None,
        warning_substr: str,
    ) -> None:
        """[tier-1/integration] execute_one_step: no_tty or a missing failure_prompter degrades PROMPT_USER to abort, never invoking the prompter."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter, **ctx_kwargs)
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()
        state = _state(tmp_path)

        action, _, error_message = StepCoordinator(context).execute_one_step(
            state, step, idx=1, total=1, step_index=0, step_context=None
        )

        assert action == "abort"
        assert error_message == "Step 'fail' failed: Command failed with exit code 1."
        assert len(state.warnings) == 1
        assert warning_substr in state.warnings[0]
        if prompter is not None:
            assert prompter.calls == 0

    def test_prompt_user_persists_checkpoint_before_prompting_and_clears_after(self, tmp_path: Path) -> None:
        """[tier-1/integration] execute_one_step: a PROMPT_USER failure saves a checkpoint via pause_store before prompting and clears it after the decision."""
        store = _InMemoryPauseStore()
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter, pause_store=store)
        step = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        StepCoordinator(context).execute_one_step(
            _state(tmp_path), step, idx=1, total=1, step_index=0, step_context=None
        )

        assert len(store.checkpoints) == 1
        assert store.checkpoints[0].pending_step_id == "fail"
        assert store.cleared == 1


class StepCoordinatorResumePendingGateTests:
    """[tier-1/integration] StepCoordinator.resume_pending_gate: re-prompt at a paused step without re-executing it first."""

    def test_reprompts_pending_step_without_reexecuting_it(self, tmp_path: Path) -> None:
        """[tier-1/integration] resume_pending_gate: an ABORT decision returns the checkpoint's pending_result unchanged, never re-running the command."""
        pending = StepResult(
            step_id="fail",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message="Command failed with exit code 1.",
        )
        checkpoint = RunCheckpoint(
            next_step_index=0,
            pending_step_id="fail",
            diagnostic="Step 'fail' failed: Command failed with exit code 1.",
            pending_result=pending,
        )
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter)
        step = (
            StepBuilder.command("echo should-not-run")
            .with_id("fail")
            .with_on_failure(FailurePolicy.PROMPT_USER)
            .build()
        )

        action, result, error_message = StepCoordinator(context).resume_pending_gate(
            _state(tmp_path), step, checkpoint, 0
        )

        assert action == "abort"
        assert result == pending
        assert error_message == "Step 'fail' failed: Command failed with exit code 1."
        assert prompter.calls == 1

    def test_retry_decision_reexecutes_the_pending_step(self, tmp_path: Path) -> None:
        """[tier-1/integration] resume_pending_gate: a RETRY decision re-runs the step via execute_one_step with attempts continuing from the checkpoint."""
        pending = StepResult(
            step_id="fail",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            attempts=1,
            error_message="Command failed with exit code 1.",
        )
        checkpoint = RunCheckpoint(
            next_step_index=0,
            pending_step_id="fail",
            diagnostic="Step 'fail' failed: Command failed with exit code 1.",
            pending_result=pending,
        )
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, failure_prompter=prompter)
        step = StepBuilder.command("exit 0").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()

        action, result, error_message = StepCoordinator(context).resume_pending_gate(
            _state(tmp_path), step, checkpoint, 0
        )

        assert action == "continue"
        assert result is not None
        assert (result.status, result.exit_code, result.attempts) == ("completed", 0, 2)
        assert error_message is None
