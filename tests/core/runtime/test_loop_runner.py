from __future__ import annotations

import dataclasses
from collections import Counter
from pathlib import Path

import pytest

from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.db import RunStatus
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.runtime import run_steps
from worktree.core.runtime.failure import USER_CONTINUED_MARKER
from worktree.core.runtime.loop_runner import LoopBlockRunner
from worktree.core.runtime.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunCheckpoint,
    RunContext,
    RunLogEvent,
    RunLogEventType,
    RunObserver,
    RunPauseStore,
    StepLoopState,
)
from worktree.core.runtime.step_coordinator import StepCoordinator
from worktree.core.step.models import (
    ArtifactPublishSpec,
    ConditionEvaluationResult,
    LoopStepBlock,
    StepAssert,
    StepDefinition,
    StepResult,
    StepType,
)


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


_LOOP_EVENT_TYPES = {
    RunLogEventType.LOOP_START,
    RunLogEventType.LOOP_TURN_START,
    RunLogEventType.LOOP_CONDITIONS_EVALUATED,
    RunLogEventType.LOOP_DONE,
}


def _coordinator_for(
    tmp_path: Path,
    *,
    failure_prompter: FailurePrompter | None = None,
    no_tty: bool = False,
) -> StepCoordinator:
    """Build a StepCoordinator whose RunContext carries the sub-step failure-prompt wiring under test."""
    return StepCoordinator(
        RunContext(
            steps=[],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=failure_prompter,
            no_tty=no_tty,
            paths=_paths_for(tmp_path),
        )
    )


class LoopSubStepPathsIdentityTests:
    """[tier-2/unit] Loop context copies retain the original path snapshot."""

    def test_loop_coordinator_replace_carries_paths_unchanged(self, tmp_path: Path) -> None:
        """dataclasses.replace preserves paths object identity when removing the pause store."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, paths=_paths_for(tmp_path))

        loop_context = dataclasses.replace(context, pause_store=None)

        assert loop_context.paths is context.paths


class GrantingFailurePrompter(FailurePrompter):
    """Test double implementing FailurePrompter returning GRANT on loop iteration ceilings."""

    def __init__(self, grant_turns: int = 3) -> None:
        self.grant_turns = grant_turns
        self.calls: list[dict[str, object]] = []

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        return FailurePromptDecision.ABORT

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        self.calls.append(
            {
                "loop_id": loop.id,
                "iteration": iteration,
                "diagnostic": diagnostic,
            }
        )
        return LoopPromptDecision.GRANT


class RecordingRunObserver(RunObserver):
    """Test double implementing RunObserver recording loop lifecycle event sequences."""

    def __init__(self) -> None:
        self.loop_events: list[tuple[object, ...]] = []

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        pass

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        pass

    def on_step_output(
        self,
        idx: int,
        total: int,
        step: StepDefinition,
        line: str,
        stream: str = "stdout",
    ) -> None:
        pass

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
        pass

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        self.loop_events.append(("on_loop_start", loop_id, max_iterations))

    def on_loop_turn_start(self, loop_id: str, turn: int, max_iterations: int) -> None:
        self.loop_events.append(("on_loop_turn_start", loop_id, turn, max_iterations))

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_turn: int | None = None,
    ) -> None:
        pass

    def on_loop_done(self, loop_id: str, status: str, turns: int) -> None:
        self.loop_events.append(("on_loop_done", loop_id, status, turns))

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        pass


class LoopIterationCeilingTests:
    """Integration tests verifying loop runner behavior when hitting max_iterations ceilings."""

    def test_loop_runner_grant_decision_extends_max_iterations(self, tmp_path: Path) -> None:
        """Verify prompt GRANT decision increases max_iterations and allows loop completion."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 2"],
            do=[StepDefinition(id="tick", type=StepType.COMMAND, command="echo ok")],
            on_max_iterations=FailurePolicy.PROMPT_USER,
        )
        prompter = GrantingFailurePrompter(grant_turns=3)
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(
            loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path), failure_prompter=prompter
        )

        action, results, error = runner.run(state)

        assert action == LoopPromptDecision.CONTINUE
        assert error is None
        assert len(results) == 2
        assert prompter.calls == [
            {
                "loop_id": "test-loop",
                "iteration": 1,
                "diagnostic": "Reached max_iterations (1) without meeting 'until' conditions.",
            }
        ]

    def test_loop_runner_on_max_iterations_continue_emits_warning_and_completes(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify on_max_iterations=continue finishes with status completed and records a warning."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=2,
            until=["iteration.index >= 5"],
            do=[StepDefinition(id="tick", type=StepType.COMMAND, command="echo ok")],
            on_max_iterations=FailurePolicy.CONTINUE,
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path))

        action, results, error = runner.run(state)

        assert action == LoopPromptDecision.CONTINUE
        assert error is None
        assert len(results) == 2
        assert state.warnings == [
            "Loop 'test-loop' reached max_iterations (2) without meeting 'until' conditions; continuing."
        ]


class LoopObserverEventTests:
    """Integration tests verifying loop lifecycle event dispatching to observers."""

    def test_loop_runner_dispatches_turn_start_and_done_events(self, tmp_path: Path) -> None:
        """Verify observer receives on_loop_start, on_loop_turn_start, and on_loop_done events."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=3,
            until=["iteration.index >= 2"],
            do=[StepDefinition(id="tick", type=StepType.COMMAND, command="echo ok")],
        )
        observer = RecordingRunObserver()
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(
            loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path), observer=observer
        )

        action, results, error = runner.run(state)

        assert action == LoopPromptDecision.CONTINUE
        assert error is None
        assert len(results) == 2
        assert observer.loop_events == [
            ("on_loop_start", "test-loop", 3),
            ("on_loop_turn_start", "test-loop", 1, 3),
            ("on_loop_turn_start", "test-loop", 2, 3),
            ("on_loop_done", "test-loop", "completed", 2),
        ]


class LoopBlockRunnerAttemptLogFilenameTests:
    """[tier-1/integration] LoopBlockRunner: loop sub-step attempt logs are disambiguated per turn."""

    def test_loop_sub_step_writes_distinct_log_file_per_turn_not_overwritten(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a two-turn loop's sub-step 'check' writes _iter_1 and _iter_2 log files, each holding only its own turn's output."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=3,
            until=["iteration.index >= 2"],
            do=[StepDefinition(id="check", type=StepType.COMMAND, command='echo "turn $WT_ITERATION_INDEX"')],
        )
        state = StepLoopState(target_dir=tmp_path, session=None, session_log_dir=log_dir)
        runner = LoopBlockRunner(
            loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path), session_log_dir=log_dir
        )

        runner.run(state)

        assert (log_dir / "01_check_iter_1_attempt_1.stdout.log").read_text(encoding="utf-8") == "turn 1\n"
        assert (log_dir / "01_check_iter_2_attempt_1.stdout.log").read_text(encoding="utf-8") == "turn 2\n"


class LoopBlockRunnerRunLogTimelineTests:
    """[tier-1/integration] LoopBlockRunner: loop lifecycle records in run.log."""

    def test_loop_block_runner_writes_turn_and_condition_events_to_run_log(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a loop meeting its until condition on turn 2 writes LOOP_START, per-turn LOOP_TURN_START/LOOP_CONDITIONS_EVALUATED, and one LOOP_DONE, interleaved with the sub-step's own STEP_START/STEP_DONE events."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=3,
            until=["iteration.index >= 2"],
            do=[StepDefinition(id="tick", type=StepType.COMMAND, command="echo ok")],
        )
        state = StepLoopState(target_dir=tmp_path, session=None, session_log_dir=tmp_path)
        runner = LoopBlockRunner(
            loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path), session_log_dir=tmp_path
        )

        runner.run(state)

        events = [
            RunLogEvent.model_validate_json(line)
            for line in (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
        ]
        loop_events = [(e.event, e.turn, e.all_passed, e.status) for e in events if e.event in _LOOP_EVENT_TYPES]
        assert loop_events == [
            (RunLogEventType.LOOP_START, None, None, None),
            (RunLogEventType.LOOP_TURN_START, 1, None, None),
            (RunLogEventType.LOOP_CONDITIONS_EVALUATED, None, False, None),
            (RunLogEventType.LOOP_TURN_START, 2, None, None),
            (RunLogEventType.LOOP_CONDITIONS_EVALUATED, None, True, None),
            (RunLogEventType.LOOP_DONE, 2, None, "completed"),
        ]

    def test_loop_two_sub_steps_two_turns_emits_step_events_for_each_sub_step(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a 2-sub-step loop run for 2 turns writes exactly 1 LOOP_START, 2 LOOP_TURN_START, 2 LOOP_CONDITIONS_EVALUATED, 1 LOOP_DONE, and 4 STEP_START/STEP_DONE events (2 sub-steps x 2 turns), since sub-steps now dispatch through StepCoordinator.execute_one_step exactly like top-level steps."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=2,
            until=["iteration.index >= 2"],
            do=[
                StepDefinition(id="a", type=StepType.COMMAND, command="echo a"),
                StepDefinition(id="b", type=StepType.COMMAND, command="echo b"),
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None, session_log_dir=tmp_path)
        runner = LoopBlockRunner(
            loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path), session_log_dir=tmp_path
        )

        runner.run(state)

        events = [
            RunLogEvent.model_validate_json(line)
            for line in (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
        ]
        counts = Counter(e.event for e in events)
        assert counts[RunLogEventType.LOOP_START] == 1
        assert counts[RunLogEventType.LOOP_TURN_START] == 2
        assert counts[RunLogEventType.LOOP_CONDITIONS_EVALUATED] == 2
        assert counts[RunLogEventType.LOOP_DONE] == 1
        assert counts[RunLogEventType.STEP_START] == 4
        assert counts[RunLogEventType.STEP_DONE] == 4


class LoopSubStepLogEventStepIndexTests:
    """[tier-1/integration] StepCoordinator.execute_one_step via LoopBlockRunner: loop sub-step STEP_START/STEP_DONE step_index is loop-body-relative."""

    def test_loop_two_sub_steps_step_index_matches_position_in_loop_do(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a loop with 2 sub-steps run for 1 turn writes STEP_START/STEP_DONE with step_index 1 for the first sub-step and 2 for the second, matching their 1-based position in loop.do."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(id="a", type=StepType.COMMAND, command="echo a"),
                StepDefinition(id="b", type=StepType.COMMAND, command="echo b"),
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None, session_log_dir=tmp_path)
        runner = LoopBlockRunner(
            loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path), session_log_dir=tmp_path
        )

        runner.run(state)

        events = [
            RunLogEvent.model_validate_json(line)
            for line in (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
        ]
        step_events = [(e.event, e.step_index, e.step_id) for e in events if e.event == RunLogEventType.STEP_START]
        assert step_events == [
            (RunLogEventType.STEP_START, 1, "a"),
            (RunLogEventType.STEP_START, 2, "b"),
        ]

    def test_loop_two_sub_steps_wt_step_index_env_var_matches_position_in_loop_do(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a loop sub-step's command sees $WT_STEP_INDEX equal to its 1-based position in loop.do, not a run-global index."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(id="a", type=StepType.COMMAND, command="echo $WT_STEP_INDEX"),
                StepDefinition(id="b", type=StepType.COMMAND, command="echo $WT_STEP_INDEX"),
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path))

        _, results, _ = runner.run(state)

        assert [r.stdout for r in results] == ["1\n", "2\n"]


class LoopSubStepAutoPublishTests:
    """[tier-1/unit] LoopBlockRunner._execute_turn: declarative artifacts: block on a do: sub-step."""

    def test_loop_sub_step_artifacts_block_publishes_on_successful_turn(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] LoopBlockRunner._execute_turn: a do: sub-step declaring artifacts: publishes via the same auto_publish_step_artifacts call a top-level step uses, once per successful turn."""
        (tmp_path / "out.txt").write_text("hi", encoding="utf-8")
        loop = LoopStepBlock(
            id="publish-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="tick",
                    type=StepType.COMMAND,
                    command="echo ok",
                    artifacts=[ArtifactPublishSpec(name="out", path="out.txt")],
                )
            ],
        )
        state = StepLoopState(
            target_dir=tmp_path,
            session=None,
            artifacts_dir=tmp_path / "artifacts",
            artifacts_db=artifacts_repository,
        )
        coordinator = StepCoordinator(
            RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id="wf_abc123", paths=_paths_for(tmp_path))
        )
        runner = LoopBlockRunner(
            loop=loop,
            sandbox_path=tmp_path,
            coordinator=coordinator,
        )

        runner.run(state)

        record = artifacts_repository.get("wf_abc123", "out")
        assert record is not None
        assert record.file_count == 1
        assert state.warnings == []


class LoopSubStepRetryHonoredTests:
    """[tier-1/integration] LoopBlockRunner._execute_turn: a loop sub-step's declared retry budget is honored, not overridden to abort."""

    def test_loop_substep_retry_runs_until_success_with_correct_attempts(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner._execute_turn: a loop sub-step declaring on_failure retry/max_retries=3 against a primitive that fails twice then succeeds records StepResult(attempts=3, status="completed"), matching top-level RunStepsFailurePromptTests retry parity."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="flaky",
                    type=StepType.COMMAND,
                    command='if [ "$WT_STEP_ATTEMPT" -ge 3 ]; then exit 0; else exit 1; fi',
                    on_failure=OnFailureSpec(action=FailurePolicy.RETRY, max_retries=3, backoff_ms=0),
                )
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path))

        action, results, error = runner.run(state)

        assert action == LoopPromptDecision.CONTINUE
        assert error is None
        assert len(results) == 1
        assert results[0].attempts == 3
        assert results[0].status == "completed"


class LoopSubStepContinueHonoredTests:
    """[tier-1/integration] LoopBlockRunner._execute_turn: a loop sub-step's terminal continue policy marks the result ignored without the prompt-continuation marker."""

    def test_loop_substep_continue_marks_ignored_without_prompt_marker(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner._execute_turn: a loop sub-step declaring on_failure continue against a failing primitive records StepResult(status="ignored") whose error_message does not contain USER_CONTINUED_MARKER, since no FailurePrompter was ever configured or consulted."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="flaky",
                    type=StepType.COMMAND,
                    command="exit 1",
                    on_failure=OnFailureSpec(action=FailurePolicy.CONTINUE),
                )
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path))

        action, results, error = runner.run(state)

        assert action == LoopPromptDecision.CONTINUE
        assert error is None
        assert results[-1].status == "ignored"
        assert USER_CONTINUED_MARKER not in (results[-1].error_message or "")


class _ScriptedFailurePrompter(FailurePrompter):
    """Test double returning a scripted queue of FailurePromptDecision values for loop sub-step failures."""

    def __init__(self, decisions: list[FailurePromptDecision]) -> None:
        self.decisions = list(decisions)

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        return self.decisions.pop(0)

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        raise AssertionError("prompt_loop_max_iterations should not be called")


class LoopSubStepPromptUserParityTests:
    """[tier-1/integration] LoopBlockRunner._execute_turn: prompt_user decision parity with the equivalent top-level step outcome."""

    @pytest.mark.parametrize(
        ("command", "decisions", "expected_status", "expected_attempts", "expect_abort"),
        [
            pytest.param(
                'if [ "$WT_STEP_ATTEMPT" -eq 1 ]; then exit 1; else exit 0; fi',
                [FailurePromptDecision.RETRY],
                "completed",
                2,
                False,
                id="retry",
            ),
            pytest.param("exit 1", [FailurePromptDecision.CONTINUE], "ignored", 1, False, id="continue"),
            pytest.param("exit 1", [FailurePromptDecision.ABORT], "failed", 1, True, id="abort"),
        ],
    )
    def test_loop_substep_prompt_user_decision_matches_top_level_outcome_category(
        self,
        tmp_path: Path,
        command: str,
        decisions: list[FailurePromptDecision],
        expected_status: str,
        expected_attempts: int,
        expect_abort: bool,
    ) -> None:
        """[tier-1/integration] LoopBlockRunner._execute_turn: for RETRY/CONTINUE/ABORT decisions, the sub-step's final status/attempts and the run's continue-vs-abort action match the equivalent top-level RunStepsFailurePromptTests contract for the same decision."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="check",
                    type=StepType.COMMAND,
                    command=command,
                    on_failure=OnFailureSpec(action=FailurePolicy.PROMPT_USER),
                )
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(
            loop=loop,
            sandbox_path=tmp_path,
            coordinator=_coordinator_for(tmp_path, failure_prompter=_ScriptedFailurePrompter(decisions)),
        )

        action, results, error = runner.run(state)

        assert results[-1].status == expected_status
        assert results[-1].attempts == expected_attempts
        if expect_abort:
            assert action == LoopPromptDecision.ABORT
            assert error is not None
            assert "check" in error
        else:
            assert action == LoopPromptDecision.CONTINUE


class _RefusingFailurePrompter(FailurePrompter):
    """Test double proving the no-tty short-circuit never consults the prompter."""

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        raise AssertionError("prompt_step_failure should not be called")

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        raise AssertionError("prompt_loop_max_iterations should not be called")


class LoopSubStepNoTtyAbortTests:
    """[tier-1/integration] LoopBlockRunner._execute_turn: no-tty degrade-to-abort parity with the top-level no-tty warning shape."""

    def test_loop_substep_prompt_user_no_tty_aborts_without_consulting_prompter(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner._execute_turn: with no_tty=True, a prompt_user sub-step failure aborts without calling the configured FailurePrompter, and state.warnings gains one entry naming the sub-step id and 'non-interactive', matching the shape of RunStepsFailurePromptTests' no_tty case."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="check",
                    type=StepType.COMMAND,
                    command="exit 1",
                    on_failure=OnFailureSpec(action=FailurePolicy.PROMPT_USER),
                )
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(
            loop=loop,
            sandbox_path=tmp_path,
            coordinator=_coordinator_for(tmp_path, failure_prompter=_RefusingFailurePrompter(), no_tty=True),
        )

        action, results, _ = runner.run(state)

        assert action == LoopPromptDecision.ABORT
        assert results[-1].status == "failed"
        assert len(state.warnings) == 1
        assert "check" in state.warnings[0]
        assert "non-interactive" in state.warnings[0]


class LoopSubStepAssertFailureParityTests:
    """[tier-1/integration] LoopBlockRunner._execute_turn: assert_ failure escalates identically to a process failure."""

    def test_loop_substep_assert_failure_marks_failed_with_pinned_message_format(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner._execute_turn: a sub-step exiting 0 but failing assert_(output_contains='never-appears') records StepResult(status='failed', exit_code=0, error_message="Step 'check' failed assertion checks:\n  [FAIL] output_contains: substring 'never-appears' not found in output")."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="check",
                    type=StepType.COMMAND,
                    command="echo ok",
                    assert_=StepAssert(output_contains="never-appears"),
                )
            ],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path))

        _, results, _ = runner.run(state)

        result = results[-1]
        assert result.status == "failed"
        assert result.exit_code == 0
        assert result.error_message == (
            "Step 'check' failed assertion checks:\n  [FAIL] output_contains: substring 'never-appears' not found in output"
        )


class LoopCrossTurnHistoricalVisibilityTests:
    """[tier-1/integration] LoopBlockRunner._execute_turn: a turn-2 sub-step sees turn-1's sub-step result as previous_step, unchanged by moving result-recording out of the loop."""

    def test_loop_second_turn_substep_sees_first_turn_substep_as_previous_step(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a 2-turn loop's sub-step 'check' echoing {{ previous_step.status }} on turn 2 prints the string "completed", proving turn-1's result remains visible even though LoopBlockRunner never appends it to state.step_results."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=2,
            until=["iteration.index >= 2"],
            do=[StepDefinition(id="check", type=StepType.COMMAND, command="echo {{ previous_step.status }}")],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, coordinator=_coordinator_for(tmp_path))

        action, results, error = runner.run(state)

        assert action == LoopPromptDecision.CONTINUE
        assert error is None
        assert len(results) == 2
        assert results[1].stdout == "completed\n"


class _InterruptingFailurePrompter(FailurePrompter):
    """Test double whose prompt_step_failure raises KeyboardInterrupt to simulate a mid-prompt interrupt."""

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        raise KeyboardInterrupt

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        raise AssertionError("prompt_loop_max_iterations should not be called")


class _RecordingPauseStore(RunPauseStore):
    """Test double recording every persisted checkpoint."""

    def __init__(self) -> None:
        self.checkpoints: list[RunCheckpoint] = []

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        self.checkpoints.append(checkpoint)

    def clear_pause(self) -> None:
        pass


class LoopSubStepPromptUserInterruptNeverPersistsCheckpointTests:
    """[tier-1/integration] LoopBlockRunner via StepCoordinator._prompt_user_decision: a loop sub-step's prompt_user KeyboardInterrupt never persists a checkpoint, even when the top-level run has a pause_store configured."""

    def test_loop_substep_keyboard_interrupt_never_saves_checkpoint_and_cancels_run(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: a loop sub-step declaring on_failure prompt_user whose FailurePrompter raises KeyboardInterrupt yields RunOutcome(status=RunStatus.CANCELLED) and RunPauseStore.save_checkpoint is never called, even though RunContext.pause_store is set, because _dispatch_step builds the loop's StepCoordinator from dataclasses.replace(context, pause_store=None)."""
        pause_store = _RecordingPauseStore()
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=1,
            until=["iteration.index >= 1"],
            do=[
                StepDefinition(
                    id="check",
                    type=StepType.COMMAND,
                    command="exit 1",
                    on_failure=OnFailureSpec(action=FailurePolicy.PROMPT_USER),
                )
            ],
        )
        context = RunContext(
            steps=[loop],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=_InterruptingFailurePrompter(),
            pause_store=pause_store,
            paths=_paths_for(tmp_path),
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.CANCELLED
        assert pause_store.checkpoints == []
