from __future__ import annotations

from pathlib import Path

from worktree.common.models import FailurePolicy
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.runtime.loop_runner import LoopBlockRunner
from worktree.core.runtime.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunLogEvent,
    RunLogEventType,
    RunObserver,
    StepLoopState,
)
from worktree.core.step.models import (
    ArtifactPublishSpec,
    ConditionEvaluationResult,
    LoopStepBlock,
    StepDefinition,
    StepResult,
    StepType,
)


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
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, failure_prompter=prompter)

        action, result, error = runner.run(state)

        assert (action, result, error) == (LoopPromptDecision.CONTINUE, None, None)
        assert prompter.calls == [
            {
                "loop_id": "test-loop",
                "iteration": 1,
                "diagnostic": "Reached max_iterations (1) without meeting 'until' conditions.",
            }
        ]
        assert len(state.step_results) == 2

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
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path)

        action, result, error = runner.run(state)

        assert (action, result, error) == (LoopPromptDecision.CONTINUE, None, None)
        assert state.warnings == [
            "Loop 'test-loop' reached max_iterations (2) without meeting 'until' conditions; continuing."
        ]
        assert len(state.step_results) == 2


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
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, observer=observer)

        action, result, error = runner.run(state)

        assert (action, result, error) == (LoopPromptDecision.CONTINUE, None, None)
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
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, session_log_dir=log_dir)

        runner.run(state)

        assert (log_dir / "01_check_iter_1_attempt_1.stdout.log").read_text(encoding="utf-8") == "turn 1\n"
        assert (log_dir / "01_check_iter_2_attempt_1.stdout.log").read_text(encoding="utf-8") == "turn 2\n"


class LoopBlockRunnerRunLogTimelineTests:
    """[tier-1/integration] LoopBlockRunner: loop lifecycle records in run.log."""

    def test_loop_block_runner_writes_turn_and_condition_events_to_run_log(self, tmp_path: Path) -> None:
        """[tier-1/integration] LoopBlockRunner: a loop meeting its until condition on turn 2 writes LOOP_START, per-turn LOOP_TURN_START/LOOP_CONDITIONS_EVALUATED, and one LOOP_DONE."""
        loop = LoopStepBlock(
            id="test-loop",
            type="loop",
            max_iterations=3,
            until=["iteration.index >= 2"],
            do=[StepDefinition(id="tick", type=StepType.COMMAND, command="echo ok")],
        )
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(loop=loop, sandbox_path=tmp_path, session_log_dir=tmp_path)

        runner.run(state)

        events = [
            RunLogEvent.model_validate_json(line)
            for line in (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
        ]
        assert [(e.event, e.turn, e.all_passed, e.status) for e in events] == [
            (RunLogEventType.LOOP_START, None, None, None),
            (RunLogEventType.LOOP_TURN_START, 1, None, None),
            (RunLogEventType.LOOP_CONDITIONS_EVALUATED, None, False, None),
            (RunLogEventType.LOOP_TURN_START, 2, None, None),
            (RunLogEventType.LOOP_CONDITIONS_EVALUATED, None, True, None),
            (RunLogEventType.LOOP_DONE, 2, None, "completed"),
        ]


class LoopSubStepAutoPublishTests:
    """[tier-1/unit] LoopBlockRunner._execute_sub_step_attempt: declarative artifacts: block on a do: sub-step."""

    def test_loop_sub_step_artifacts_block_publishes_on_successful_turn(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] LoopBlockRunner._execute_sub_step_attempt: a do: sub-step declaring artifacts: publishes via the same auto_publish_step_artifacts call a top-level step uses, once per successful turn."""
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
        state = StepLoopState(target_dir=tmp_path, session=None)
        runner = LoopBlockRunner(
            loop=loop,
            sandbox_path=tmp_path,
            session_id="wf_abc123",
            artifacts_dir=tmp_path / "artifacts",
            artifacts_db=artifacts_repository,
        )

        runner.run(state)

        record = artifacts_repository.get("wf_abc123", "out")
        assert record is not None
        assert record.file_count == 1
        assert state.warnings == []
