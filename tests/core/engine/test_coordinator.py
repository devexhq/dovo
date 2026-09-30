"""Contract tests for RunCoordinator: durable leaf transitions, prompt recovery, loops, and sandbox identity."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

from tests.harness.runs import SEEDED_FAILURE, NoOpRunObserver, seed_new_run, seed_paused_run
from worktree.common.filesystem.models import WorkspacePaths
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.engine import RunCoordinator, RunStateStore
from worktree.core.engine.context import RunSessionContext
from worktree.core.engine.coordinator import NodeTransitionKind
from worktree.core.engine.failure import USER_CONTINUED_MARKER
from worktree.core.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunObserver,
    RunOutcome,
)
from worktree.core.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from worktree.core.engine.writer import load_blueprint_from_snapshot, snapshot_blueprint_path
from worktree.core.logs import RunLogEvent, RunLogEventType
from worktree.core.logs.services.read import read_run_log_events
from worktree.core.sandbox.models import SandboxSession
from worktree.core.step import StepExecution
from worktree.core.step.models import ConditionEvaluationResult, LoopStepBlock, StepDefinition, StepResult

_FAIL_DETAIL = "Command failed with exit code 1."


def _timeline_entry(event: RunLogEvent) -> tuple[str, dict[str, object]]:
    """Return the event name and details() minus the non-deterministic duration."""
    return event.event.value, {k: v for k, v in event.details().items() if k != "duration_seconds"}


def _loop_body_entries(iteration: int) -> list[tuple[str, dict[str, object]]]:
    """Return the pinned s1/s2 step_start/step_done entries for one iteration of loop "loop"."""
    loop_fields: dict[str, object] = {"loop_id": "loop", "iteration": iteration}
    done: dict[str, object] = {"attempt": 1, "status": "completed", "exit_code": 0, **loop_fields}
    return [
        ("step_start", {"step_index": 1, "step_id": "s1", "attempt": 1, **loop_fields}),
        ("step_done", {"step_index": 1, "step_id": "s1", **done}),
        ("step_start", {"step_index": 2, "step_id": "s2", "attempt": 1, **loop_fields}),
        ("step_done", {"step_index": 2, "step_id": "s2", **done}),
    ]


class _Prompter(FailurePrompter):
    """Scripted FailurePrompter recording each prompted result; can run a hook or raise KeyboardInterrupt on prompt."""

    def __init__(
        self,
        decisions: list[FailurePromptDecision] | None = None,
        *,
        on_prompt: Callable[[], None] | None = None,
        interrupt: bool = False,
        loop_decisions: list[LoopPromptDecision] | None = None,
        interrupt_loop: bool = False,
    ) -> None:
        self.decisions = list(decisions or [])
        self.prompted: list[StepResult] = []
        self.on_prompt = on_prompt
        self.interrupt = interrupt
        self.loop_decisions = list(loop_decisions or [])
        self.loop_prompts: list[tuple[int, int]] = []
        self.interrupt_loop = interrupt_loop

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        self.prompted.append(result)
        if self.on_prompt is not None:
            self.on_prompt()
        if self.interrupt:
            raise KeyboardInterrupt
        return self.decisions.pop(0)

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        self.loop_prompts.append((iteration, grant_count))
        if self.interrupt_loop:
            raise KeyboardInterrupt
        return self.loop_decisions.pop(0)


class _SequenceObserver(NoOpRunObserver):
    """RunObserver recording every step and loop callback in one ordered list."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self.calls.append(("step_start", idx, total, step.id))

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        self.calls.append(("step_done", idx, total, step.id, result.status))

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        self.calls.append(("loop_start", loop_id, max_iterations))

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        self.calls.append(("loop_iteration_start", loop_id, iteration, max_iterations))

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        self.calls.append(
            ("loop_conditions_evaluated", loop_id, [r.passed for r in results], all_passed, next_iteration)
        )

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        self.calls.append(("loop_done", loop_id, status, total_iterations))


class _RaisingObserver(NoOpRunObserver):
    """RunObserver raising from every step callback."""

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        raise RuntimeError("observer start")

    def on_step_output(
        self,
        idx: int,
        total: int,
        step: StepDefinition,
        line: str,
        stream: str = "stdout",
    ) -> None:
        raise RuntimeError("observer output")

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        raise RuntimeError("observer done")


def _step(step_id: str, run: str, **extra: object) -> dict[str, object]:
    return {"id": step_id, "run": run, **extra}


def _loop(
    loop_id: str,
    do: list[dict[str, object]],
    *,
    max_iterations: int = 2,
    until: list[str] | None = None,
    on_max_iterations: str | None = None,
) -> dict[str, object]:
    loop: dict[str, object] = {
        "id": loop_id,
        "type": "loop",
        "max_iterations": max_iterations,
        "until": until if until is not None else [f"iteration.index >= {max_iterations}"],
        "do": do,
    }
    if on_max_iterations is not None:
        loop["on_max_iterations"] = on_max_iterations
    return loop


def _context(
    paths: WorkspacePaths,
    session_id: str,
    *,
    sandbox: SandboxSession | None = None,
    no_tty: bool = False,
    artifacts_db: ArtifactsRepository | None = None,
) -> RunSessionContext:
    """Session context with scratch and log directories created like drive_run does."""
    session_tmp_dir = paths.tmp_dir / session_id
    (session_tmp_dir / "steps").mkdir(parents=True, exist_ok=True)
    session_log_dir = paths.logs_dir / session_id
    session_log_dir.mkdir(parents=True, exist_ok=True)
    return RunSessionContext(
        session_id=session_id,
        paths=paths,
        target_dir=paths.root_dir,
        session_tmp_dir=session_tmp_dir,
        session_log_dir=session_log_dir,
        artifacts_dir=paths.artifacts_dir if artifacts_db is not None else None,
        artifacts_db=artifacts_db,
        sandbox=sandbox,
        no_tty=no_tty,
    )


def _coordinator(
    paths: WorkspacePaths,
    runs: RunsRepository,
    session_id: str,
    *,
    prompter: FailurePrompter | None = None,
    observer: RunObserver | None = None,
    sandbox: SandboxSession | None = None,
    no_tty: bool = False,
    artifacts_db: ArtifactsRepository | None = None,
) -> RunCoordinator:
    return RunCoordinator(
        RunStateStore(runs, paths, session_id),
        _context(paths, session_id, sandbox=sandbox, no_tty=no_tty, artifacts_db=artifacts_db),
        observer=observer,
        prompter=prompter,
    )


def _run_new(
    paths: WorkspacePaths,
    runs: RunsRepository,
    session_id: str,
    steps: list[dict[str, object]],
    *,
    prompter: FailurePrompter | None = None,
    observer: RunObserver | None = None,
    no_tty: bool = False,
    artifacts_db: ArtifactsRepository | None = None,
) -> RunOutcome:
    """Seed a fresh run and execute it through RunCoordinator."""
    seed_new_run(paths, runs, session_id=session_id, steps=steps)
    return _coordinator(
        paths, runs, session_id, prompter=prompter, observer=observer, no_tty=no_tty, artifacts_db=artifacts_db
    ).execute()


def _state(paths: WorkspacePaths, runs: RunsRepository, session_id: str) -> ExecutionStateTree:
    state = RunStateStore(runs, paths, session_id).load().state
    assert state is not None
    return state


def _leaf(state: ExecutionStateTree, step_id: str) -> ExecutionLeafNode:
    for node in state.nodes:
        if isinstance(node, ExecutionLeafNode) and node.id == step_id:
            return node
    raise AssertionError(f"no leaf {step_id}")


def _loop_node(state: ExecutionStateTree, loop_id: str = "loop") -> ExecutionLoopNode:
    for node in state.nodes:
        if isinstance(node, ExecutionLoopNode) and node.id == loop_id:
            return node
    raise AssertionError(f"no loop {loop_id}")


def _run_log(paths: WorkspacePaths, session_id: str) -> list[dict[str, object]]:
    lines = (paths.logs_dir / session_id / "run.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _last_result(node: ExecutionLeafNode) -> StepResult:
    result = node.attempts[-1].result
    assert result is not None
    return result


@pytest.fixture
def saved_statuses(monkeypatch: pytest.MonkeyPatch) -> list[RunStatus | str | None]:
    """Record the run status carried by every execution-state save."""
    recorded: list[RunStatus | str | None] = []
    real = RunsRepository.save_execution_state

    def spy(
        self: RunsRepository,
        session_id: str,
        execution_state_json: str,
        *,
        expected_revision: int,
        next_revision: int,
        status: RunStatus | str | None = None,
        error_message: str | None = None,
        sandbox_id: str | None = None,
    ):
        recorded.append(status)
        return real(
            self,
            session_id,
            execution_state_json,
            expected_revision=expected_revision,
            next_revision=next_revision,
            status=status,
            error_message=error_message,
            sandbox_id=sandbox_id,
        )

    monkeypatch.setattr(RunsRepository, "save_execution_state", spy)
    return recorded


@pytest.fixture
def saved_states(monkeypatch: pytest.MonkeyPatch) -> list[ExecutionStateTree]:
    """Record the parsed execution state carried by every execution-state save."""
    recorded: list[ExecutionStateTree] = []
    real = RunsRepository.save_execution_state

    def spy(
        self: RunsRepository,
        session_id: str,
        execution_state_json: str,
        *,
        expected_revision: int,
        next_revision: int,
        status: RunStatus | str | None = None,
        error_message: str | None = None,
        sandbox_id: str | None = None,
    ):
        recorded.append(ExecutionStateTree.model_validate_json(execution_state_json))
        return real(
            self,
            session_id,
            execution_state_json,
            expected_revision=expected_revision,
            next_revision=next_revision,
            status=status,
            error_message=error_message,
            sandbox_id=sandbox_id,
        )

    monkeypatch.setattr(RunsRepository, "save_execution_state", spy)
    return recorded


class CoordinatorExecuteTests:
    """[tier-1/integration] RunCoordinator.execute: durable transitions for linear runs."""

    def test_two_leaf_run_completes_with_flattened_results_and_advancing_revision(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: two echo leaves return COMPLETED with step_results ids [a, b], both nodes completed in the persisted state, and execution_state_revision greater than its initial value."""
        outcome = _run_new(engine_paths, runs_repo, "two", [_step("a", "echo a"), _step("b", "echo b")])

        assert outcome.status == RunStatus.COMPLETED
        assert [result.step_id for result in outcome.step_results] == ["a", "b"]
        state = _state(engine_paths, runs_repo, "two")
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.COMPLETED]
        row = runs_repo.get("two")
        assert row is not None
        assert (row.execution_state_revision or 0) > 0

    def test_abort_policy_failure_marks_leaf_failed_and_skips_later_leaves(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: leaf a exits 1 under abort returns FAILED with errors == ["Step 'a' failed: <detail>"], node a failed, node b pending, and b's marker file absent."""
        outcome = _run_new(engine_paths, runs_repo, "abort", [_step("a", "exit 1"), _step("b", "touch b.ran")])

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {_FAIL_DETAIL}"]
        state = _state(engine_paths, runs_repo, "abort")
        assert [node.state for node in state.nodes] == [NodeState.FAILED, NodeState.PENDING]
        assert not (engine_workspace / "b.ran").exists()

    def test_continue_policy_marks_leaf_ignored_and_runs_remaining_leaves(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: leaf a failing under continue returns COMPLETED with step_results statuses ["ignored", "completed"] and node a ignored."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "cont",
            [_step("a", "exit 1", on_failure="continue"), _step("b", "echo b")],
        )

        assert outcome.status == RunStatus.COMPLETED
        assert [result.status for result in outcome.step_results] == ["ignored", "completed"]
        assert _leaf(_state(engine_paths, runs_repo, "cont"), "a").state == NodeState.IGNORED

    def test_fresh_and_resumed_linear_runs_produce_equal_step_results(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a three-step run executed straight through and one paused at step 2 then resumed with continue return step_results with equal (step_id, status) sequences."""
        straight = _run_new(
            engine_paths,
            runs_repo,
            "straight",
            [_step("a", "true"), _step("b", "exit 1", on_failure="continue"), _step("c", "true")],
        )
        seed_paused_run(
            engine_paths,
            runs_repo,
            session_id="resumed",
            steps=[_step("a", "true"), _step("b", "exit 1", on_failure="prompt_user"), _step("c", "true")],
            paused_step_id="b",
        )

        resumed = _coordinator(
            engine_paths, runs_repo, "resumed", prompter=_Prompter([FailurePromptDecision.CONTINUE])
        ).execute()

        assert [(r.step_id, r.status) for r in resumed.step_results] == [
            (r.step_id, r.status) for r in straight.step_results
        ]
        assert [r.status for r in resumed.step_results] == ["completed", "ignored", "completed"]

    def test_unreadable_state_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a row with corrupt execution_state_json returns FAILED with errors == [store error "Execution state for run '<id>' is corrupt."] and no step marker file."""
        seed_new_run(engine_paths, runs_repo, session_id="bad", steps=[_step("a", "touch a.ran")])
        runs_repo.save_execution_state("bad", "{not json", expected_revision=0, next_revision=1)

        outcome = _coordinator(engine_paths, runs_repo, "bad").execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Execution state for run 'bad' is corrupt."]
        assert not (engine_workspace / "a.ran").exists()

    def test_persistence_failure_after_step_result_stops_dispatch_and_names_boundary(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        engine_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: save_execution_state returning None after leaf a finished returns FAILED with errors[0] starting "Failed to persist run state after step 'a' finished:" and node b's marker file absent."""
        seed_new_run(
            engine_paths, runs_repo, session_id="persist", steps=[_step("a", "echo a"), _step("b", "touch b.ran")]
        )
        real = RunsRepository.save_execution_state
        calls: list[int] = []

        def flaky(
            self: RunsRepository,
            session_id: str,
            execution_state_json: str,
            *,
            expected_revision: int,
            next_revision: int,
            status: RunStatus | str | None = None,
            error_message: str | None = None,
            sandbox_id: str | None = None,
        ):
            calls.append(next_revision)
            if len(calls) == 2:
                return None
            return real(
                self,
                session_id,
                execution_state_json,
                expected_revision=expected_revision,
                next_revision=next_revision,
                status=status,
                error_message=error_message,
                sandbox_id=sandbox_id,
            )

        monkeypatch.setattr(RunsRepository, "save_execution_state", flaky)

        outcome = _coordinator(engine_paths, runs_repo, "persist").execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors[0].startswith("Failed to persist run state after step 'a' finished:")
        assert not (engine_workspace / "b.ran").exists()

    def test_interrupted_attempt_is_closed_failed_and_never_completed(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a RUNNING leaf whose last attempt has result None ends with attempts[-1].result.status "failed" and error_message "Step attempt was interrupted before it recorded a result.", and the leaf is never completed."""
        seed_new_run(engine_paths, runs_repo, session_id="crash", steps=[_step("a", "touch a.ran")])
        store = RunStateStore(runs_repo, engine_paths, "crash")
        state = _state(engine_paths, runs_repo, "crash")
        interrupted = _leaf(state, "a")
        interrupted.state = NodeState.RUNNING
        interrupted.attempts = [StepAttemptRecord(number=1, started_at="then")]
        assert store.save(state).ok

        outcome = _coordinator(engine_paths, runs_repo, "crash").execute()

        leaf = _leaf(_state(engine_paths, runs_repo, "crash"), "a")
        assert leaf.state == NodeState.FAILED
        assert _last_result(leaf).status == "failed"
        assert _last_result(leaf).error_message == "Step attempt was interrupted before it recorded a result."
        assert outcome.status == RunStatus.FAILED
        assert not (engine_workspace / "a.ran").exists()

    def test_keyboard_interrupt_during_step_cancels_run_and_marks_node_cancelled(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: KeyboardInterrupt raised by StepExecution.run returns CANCELLED with errors == ["Execution cancelled by user."] and the in-flight node persisted cancelled."""

        def interrupted(self: StepExecution) -> StepResult:
            raise KeyboardInterrupt

        monkeypatch.setattr(StepExecution, "run", interrupted)

        outcome = _run_new(engine_paths, runs_repo, "ctrl-c", [_step("a", "echo a"), _step("b", "echo b")])

        assert outcome.status == RunStatus.CANCELLED
        assert outcome.errors == ["Execution cancelled by user."]
        state = _state(engine_paths, runs_repo, "ctrl-c")
        assert [node.state for node in state.nodes] == [NodeState.CANCELLED, NodeState.PENDING]

    def test_observer_exception_does_not_abort_run(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: an observer raising from every callback still yields COMPLETED with both step results."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "raising",
            [_step("a", "echo a"), _step("b", "echo b")],
            observer=_RaisingObserver(),
        )

        assert outcome.status == RunStatus.COMPLETED
        assert [result.step_id for result in outcome.step_results] == ["a", "b"]

    def test_retried_step_logs_final_attempt_number(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a step with retry max_retries 3 failing twice then passing records a STEP_DONE event with attempt 3 and a result with attempts == 3."""
        flaky = 'n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count; [ "$n" -ge 3 ]'
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "retry",
            [_step("a", flaky, on_failure={"action": "retry", "max_retries": 3, "backoff_ms": 0})],
        )

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.step_results[0].attempts == 3
        run_log = (engine_paths.logs_dir / "retry" / "run.log").read_text(encoding="utf-8")
        done = [json.loads(line) for line in run_log.splitlines() if '"step_done"' in line]
        assert [event["attempt"] for event in done] == [3]

    @pytest.mark.parametrize(
        ("command", "expected_artifacts"),
        [
            pytest.param("echo hi > out.txt", ["out"], id="passing-publishes"),
            pytest.param("echo hi > out.txt; exit 1", [], id="failing-publishes-nothing"),
        ],
    )
    def test_completed_leaf_with_declared_artifacts_publishes_them_through_step_executor(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        artifacts_repository: ArtifactsRepository,
        command: str,
        expected_artifacts: list[str],
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a passing leaf declaring artifacts under a context with an artifacts repository leaves those artifacts listed by the repository, and a failing leaf declaring artifacts publishes none."""
        _run_new(
            engine_paths,
            runs_repo,
            "arts",
            [_step("a", command, artifacts=[{"name": "out", "path": "out.txt"}])],
            artifacts_db=artifacts_repository,
        )

        assert [record.name for record in artifacts_repository.list(session_id="arts")] == expected_artifacts


class CoordinatorObserverContractTests:
    """[tier-1/integration] RunCoordinator.execute: observer callback sequence and run.log round-trip contracts."""

    def test_linear_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a 3-step run [a, b, c] gives one ordered observer sequence ("step_start", 1, 3, "a"), ("step_done", 1, 3, "a", "completed"), then the same pair at 2/3 for b and 3/3 for c, with no loop callbacks."""
        observer = _SequenceObserver()

        _run_new(
            engine_paths,
            runs_repo,
            "linear",
            [_step("a", "echo a"), _step("b", "echo b"), _step("c", "echo c")],
            observer=observer,
        )

        assert observer.calls == [
            ("step_start", 1, 3, "a"),
            ("step_done", 1, 3, "a", "completed"),
            ("step_start", 2, 3, "b"),
            ("step_done", 2, 3, "b", "completed"),
            ("step_start", 3, 3, "c"),
            ("step_done", 3, 3, "c", "completed"),
        ]

    def test_loop_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a 2-iteration loop of [s1, s2] gives the 14-call ordered observer sequence loop_start(2), iteration_start(1), s1 (1/2), s2 (2/2), conditions([False], False, next 2), iteration_start(2), s1 (1/2), s2 (2/2), conditions([True], True, None), loop_done("completed", 2)."""
        observer = _SequenceObserver()

        _run_new(
            engine_paths,
            runs_repo,
            "loop-seq",
            [_loop("loop", [_step("s1", "true"), _step("s2", "true")])],
            observer=observer,
        )

        assert observer.calls == [
            ("loop_start", "loop", 2),
            ("loop_iteration_start", "loop", 1, 2),
            ("step_start", 1, 2, "s1"),
            ("step_done", 1, 2, "s1", "completed"),
            ("step_start", 2, 2, "s2"),
            ("step_done", 2, 2, "s2", "completed"),
            ("loop_conditions_evaluated", "loop", [False], False, 2),
            ("loop_iteration_start", "loop", 2, 2),
            ("step_start", 1, 2, "s1"),
            ("step_done", 1, 2, "s1", "completed"),
            ("step_start", 2, 2, "s2"),
            ("step_done", 2, 2, "s2", "completed"),
            ("loop_conditions_evaluated", "loop", [True], True, None),
            ("loop_done", "loop", "completed", 2),
        ]

    def test_loop_run_log_round_trips_through_reader(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] read_run_log_events: a coordinator-written run.log for a 2-iteration loop "loop" of [s1, s2] returns one event per file line matching the pinned 14-event (event, details()) sequence, with step_start/step_done details carrying loop_id "loop", iteration 1 then 2 and step_name absent, step_done duration_seconds a float, loop_done iteration 2, and conditions passed [False] then [True]."""
        _run_new(engine_paths, runs_repo, "loop-log", [_loop("loop", [_step("s1", "true"), _step("s2", "true")])])
        log_dir = engine_paths.logs_dir / "loop-log"

        events = read_run_log_events(log_dir, tail=None)

        assert len(events) == len((log_dir / "run.log").read_text(encoding="utf-8").splitlines())
        durations = [e.duration_seconds for e in events if e.event is RunLogEventType.STEP_DONE]
        assert len(durations) == 4
        assert all(isinstance(duration, float) for duration in durations)
        assert [_timeline_entry(e) for e in events] == [
            ("loop_start", {"loop_id": "loop", "max_iterations": 2}),
            ("loop_iteration_start", {"loop_id": "loop", "iteration": 1, "max_iterations": 2}),
            *_loop_body_entries(1),
            ("loop_conditions_evaluated", {"loop_id": "loop", "all_passed": False, "next_iteration": 2}),
            ("loop_iteration_start", {"loop_id": "loop", "iteration": 2, "max_iterations": 2}),
            *_loop_body_entries(2),
            ("loop_conditions_evaluated", {"loop_id": "loop", "all_passed": True}),
            ("loop_done", {"loop_id": "loop", "status": "completed", "iteration": 2}),
        ]
        evaluated = [e for e in events if e.event is RunLogEventType.LOOP_CONDITIONS_EVALUATED]
        assert [[c["passed"] for c in e.conditions or []] for e in evaluated] == [[False], [True]]

    def test_top_level_step_events_leave_loop_fields_null(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] read_run_log_events: a coordinator-written run.log for top-level steps [a, b] has step_start/step_done events whose loop_id and iteration are None."""
        _run_new(engine_paths, runs_repo, "flat-log", [_step("a", "true"), _step("b", "true")])

        events = read_run_log_events(engine_paths.logs_dir / "flat-log", tail=None)

        step_events = [e for e in events if e.event.value in {"step_start", "step_done"}]
        assert len(step_events) == 4
        assert [(e.loop_id, e.iteration) for e in step_events] == [(None, None)] * 4

    def test_step_name_and_duration_are_logged(self, engine_paths: WorkspacePaths, runs_repo: RunsRepository) -> None:
        """[tier-1/integration] read_run_log_events: a run of [step "a" with name "Run tests", step "b" without name] gives step_start step_name "Run tests" then None, and step_done duration_seconds equal to the matching RunOutcome.step_results[i].duration_seconds."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "named-log",
            [_step("a", "true", name="Run tests"), _step("b", "true")],
        )

        events = read_run_log_events(engine_paths.logs_dir / "named-log", tail=None)

        assert [e.step_name for e in events if e.event.value == "step_start"] == ["Run tests", None]
        assert [e.duration_seconds for e in events if e.event.value == "step_done"] == [
            result.duration_seconds for result in outcome.step_results
        ]


class CoordinatorLoadTests:
    """[tier-1/integration] RunCoordinator.load and steps: memoized definition loading."""

    def test_execute_after_failed_load_returns_failed_with_load_error_once(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.load: a run whose definitions snapshot is missing gives load() False, and a following execute() returns FAILED with the load error appearing exactly once in errors."""
        seed_new_run(engine_paths, runs_repo, session_id="load-fail", steps=[_step("a", "true")])
        snapshot_blueprint_path(engine_paths.session_dir("load-fail"), "load-fail").unlink()
        coordinator = _coordinator(engine_paths, runs_repo, "load-fail")

        assert coordinator.load() is False
        outcome = coordinator.execute()

        assert outcome.status == RunStatus.FAILED
        assert len(outcome.errors) == 1

    def test_steps_returns_top_level_definitions_after_load(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.steps: after load() on a run of [step "a", loop "loop"], steps ids are ["a", "loop"]; a second load() after deleting the snapshot still returns True."""
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="load-steps",
            steps=[_step("a", "true"), _loop("loop", [_step("s1", "true")])],
        )
        coordinator = _coordinator(engine_paths, runs_repo, "load-steps")

        assert coordinator.load() is True
        snapshot_blueprint_path(engine_paths.session_dir("load-steps"), "load-steps").unlink()

        assert [step.id for step in coordinator.steps] == ["a", "loop"]
        assert coordinator.load() is True


class CoordinatorMetadataTests:
    """[tier-1/integration] RunCoordinator.execute: execution metadata reaches step environments and templates."""

    def test_first_step_sees_empty_previous_step_env_vars(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: the first step's WT_PREVIOUS_STEP_ID and WT_PREVIOUS_STEP_STATUS are empty."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-first",
            [_step("first_step", 'echo "PREV_ID=[$WT_PREVIOUS_STEP_ID] PREV_STATUS=[$WT_PREVIOUS_STEP_STATUS]"')],
        )

        assert outcome.step_results[0].stdout == "PREV_ID=[] PREV_STATUS=[]\n"

    def test_second_step_sees_previous_step_id_name_index_status_exit_code_env_vars(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: the second step's WT_PREVIOUS_STEP_* variables carry the first step's id, name, 1-based index, status, and exit code."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-prev",
            [
                _step("setup_step", "echo 'setup done'", name="Setup Step"),
                _step(
                    "verify_step",
                    'echo "PREV_ID=$WT_PREVIOUS_STEP_ID PREV_NAME=$WT_PREVIOUS_STEP_NAME '
                    "PREV_IDX=$WT_PREVIOUS_STEP_INDEX PREV_STATUS=$WT_PREVIOUS_STEP_STATUS "
                    'PREV_EXIT=$WT_PREVIOUS_STEP_EXIT_CODE"',
                ),
            ],
        )

        assert (
            outcome.step_results[1].stdout
            == "PREV_ID=setup_step PREV_NAME=Setup Step PREV_IDX=1 PREV_STATUS=completed PREV_EXIT=0\n"
        )

    def test_continue_on_failure_previous_step_status_is_ignored_with_exit_code_zero(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: after a failing step under continue, the next step sees previous status ignored and exit code 0."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-continue",
            [
                _step("failing_step", "exit 3", on_failure="continue"),
                _step(
                    "next_step",
                    'echo "PREV_ID=$WT_PREVIOUS_STEP_ID PREV_STATUS=$WT_PREVIOUS_STEP_STATUS '
                    'PREV_EXIT=$WT_PREVIOUS_STEP_EXIT_CODE"',
                ),
            ],
        )

        assert outcome.step_results[0].error_message == "Command failed with exit code 3."
        assert outcome.step_results[1].stdout == "PREV_ID=failing_step PREV_STATUS=ignored PREV_EXIT=0\n"

    def test_prompt_user_retry_increments_step_attempt_env_var_from_one_to_two(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a prompt_user retry runs the step again with WT_STEP_ATTEMPT 2 and records attempts == 2."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-retry",
            [
                _step(
                    "retry_on_prompt",
                    'if [ "$WT_STEP_ATTEMPT" -eq 1 ]; then echo fail1 >&2; exit 1; else echo success2; fi',
                    on_failure="prompt_user",
                )
            ],
            prompter=_Prompter([FailurePromptDecision.RETRY]),
        )

        assert outcome.status == RunStatus.COMPLETED
        assert (outcome.step_results[0].stdout, outcome.step_results[0].attempts) == ("success2\n", 2)

    def test_three_step_run_propagates_steps_context_and_wt_steps_json_excluding_in_flight_step(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: later steps see only finished steps through the steps template context and WT_STEPS_JSON."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-json",
            [
                _step("step_a", 'echo "A_STEPS=[{{ steps[0].id }}] A_JSON=$WT_STEPS_JSON"', name="Step Alpha"),
                _step(
                    "step_b",
                    'echo "B_LAST={{ steps[-1].id }} B_A_STAT={{ steps.step_a.status }} B_JSON=$WT_STEPS_JSON"',
                    name="Step Beta",
                ),
                _step("step_c", 'echo "C_SECOND={{ steps[1].id }} C_PREV={{ previous_step.id }}"'),
            ],
        )

        assert outcome.step_results[0].stdout == "A_STEPS=[] A_JSON=[]\n"
        assert outcome.step_results[1].stdout == (
            "B_LAST=step_a B_A_STAT=completed "
            'B_JSON=[{"id": "step_a", "name": "Step Alpha", "index": "1", '
            '"status": "completed", "exit_code": "0"}]\n'
        )
        assert outcome.step_results[2].stdout == "C_SECOND=step_b C_PREV=step_b\n"

    @pytest.mark.parametrize(
        ("producer", "consumer", "expected"),
        [
            pytest.param(
                'echo "greeting=hello" >> "$WT_OUTPUT"',
                "{{ steps.step_a.outputs.greeting }}",
                "hello\n",
                id="known-key",
            ),
            pytest.param("echo done", "[{{ steps.step_a.outputs.missing }}]", "[]\n", id="unknown-key"),
        ],
    )
    def test_downstream_step_reads_upstream_output_placeholder(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        producer: str,
        consumer: str,
        expected: str,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: step_b's '{{ steps.step_a.outputs.<key> }}' interpolates to step_a's $WT_OUTPUT value, or an empty string for an unknown key."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-outputs",
            [_step("step_a", producer), _step("step_b", f'echo "{consumer}"')],
        )

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.step_results[1].stdout == expected

    def test_final_step_sees_correctly_named_steps_for_prior_step_and_both_loop_iterations(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a run of [top-level 'first', a 2-iteration loop with one sub-step 'tick', top-level 'last'] gives 'last' a command '{{ steps[0].name }}|{{ steps[1].name }}|{{ steps[2].name }}' whose stdout is 'First|Tick|Tick'."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "meta-loop",
            [
                _step("first", "echo first", name="First"),
                _loop("loop", [_step("tick", "echo tick", name="Tick")]),
                _step("last", 'echo "{{ steps[0].name }}|{{ steps[1].name }}|{{ steps[2].name }}"'),
            ],
        )

        assert len(outcome.step_results) == 4
        assert outcome.step_results[-1].stdout == "First|Tick|Tick\n"


class CoordinatorAdvanceLeafTests:
    """[tier-1/integration] RunCoordinator.advance_leaf: one durable transition per leaf, including prompt decisions."""

    @staticmethod
    def _advance(
        paths: WorkspacePaths,
        runs: RunsRepository,
        session_id: str,
        steps: list[dict[str, object]],
        *,
        prompter: FailurePrompter | None = None,
        no_tty: bool = False,
    ) -> tuple[RunCoordinator, NodeTransitionKind]:
        """Seed a fresh run and advance its first leaf once."""
        seed_new_run(paths, runs, session_id=session_id, steps=steps)
        coordinator = _coordinator(paths, runs, session_id, prompter=prompter, no_tty=no_tty)
        state = _state(paths, runs, session_id)
        node = state.nodes[0]
        assert isinstance(node, ExecutionLeafNode)
        definition = load_blueprint_from_snapshot(paths.session_dir(session_id), state.manifest).steps[0]
        assert isinstance(definition, StepDefinition)
        return coordinator, coordinator.advance_leaf(node, definition)

    def test_pending_leaf_success_returns_completed_and_persists_state(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: a PENDING echo leaf returns NodeTransitionKind.COMPLETED, and the row's state shows the node completed with one attempt whose number is 1."""
        _, transition = self._advance(engine_paths, runs_repo, "adv-ok", [_step("a", "echo a")])

        leaf = _leaf(_state(engine_paths, runs_repo, "adv-ok"), "a")
        assert transition == NodeTransitionKind.COMPLETED
        assert leaf.state == NodeState.COMPLETED
        assert [attempt.number for attempt in leaf.attempts] == [1]

    def test_prompt_retry_decision_returns_retry_with_running_attempt_two(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: a prompt_user failure answered retry returns NodeTransitionKind.RETRY, and the persisted node is running with attempts numbered [1, 2] and attempts[-1].result None."""
        _, transition = self._advance(
            engine_paths,
            runs_repo,
            "adv-retry",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=_Prompter([FailurePromptDecision.RETRY]),
        )

        leaf = _leaf(_state(engine_paths, runs_repo, "adv-retry"), "a")
        assert transition == NodeTransitionKind.RETRY
        assert leaf.state == NodeState.RUNNING
        assert [attempt.number for attempt in leaf.attempts] == [1, 2]
        assert leaf.attempts[-1].result is None

    def test_prompt_continue_decision_ignores_node_with_user_continued_marker(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: a prompt_user failure answered continue returns CONTINUED, the node is ignored, and attempts[-1].result.error_message ends "(user continued after prompt_user)"."""
        _, transition = self._advance(
            engine_paths,
            runs_repo,
            "adv-continue",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=_Prompter([FailurePromptDecision.CONTINUE]),
        )

        leaf = _leaf(_state(engine_paths, runs_repo, "adv-continue"), "a")
        assert transition == NodeTransitionKind.CONTINUED
        assert leaf.state == NodeState.IGNORED
        assert (_last_result(leaf).error_message or "").endswith("(user continued after prompt_user)")

    def test_prompt_abort_decision_fails_node_with_step_failed_message(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a prompt_user failure answered abort fails the node and the outcome errors are ["Step '<id>' failed: <detail>"]."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "adv-abort",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=_Prompter([FailurePromptDecision.ABORT]),
        )

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {_FAIL_DETAIL}"]
        assert _leaf(_state(engine_paths, runs_repo, "adv-abort"), "a").state == NodeState.FAILED

    @pytest.mark.parametrize(
        ("no_tty", "prompter", "warning_substr"),
        [
            pytest.param(True, _Prompter(), "non-interactive", id="no-tty"),
            pytest.param(False, None, "no failure prompter", id="no-prompter"),
        ],
    )
    def test_prompt_user_without_interactive_prompter_aborts_and_never_persists_paused(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        saved_statuses: list[RunStatus | str | None],
        no_tty: bool,
        prompter: _Prompter | None,
        warning_substr: str,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: prompt_user with no_tty=True or prompter None fails the run with the warning "Warning: step '<id>' requested prompt_user but ... aborting." and no saved revision has run status paused."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "adv-noninteractive",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=prompter,
            no_tty=no_tty,
        )

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {_FAIL_DETAIL}"]
        assert len(outcome.warnings) == 1
        assert outcome.warnings[0].startswith("Warning: step 'a' requested prompt_user but")
        assert warning_substr in outcome.warnings[0]
        assert RunStatus.PAUSED not in saved_statuses
        assert prompter is None or prompter.prompted == []

    def test_prompt_persists_paused_state_before_prompter_is_consulted(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: a prompter that reads the run row when called sees status paused and the leaf PAUSED with its failed attempt result; after the decision the row is running again."""
        seen: dict[str, object] = {}

        def read_row() -> None:
            row = runs_repo.get("adv-paused")
            assert row is not None
            leaf = _leaf(_state(engine_paths, runs_repo, "adv-paused"), "a")
            seen["status"] = row.status
            seen["leaf_state"] = leaf.state
            seen["result_status"] = _last_result(leaf).status

        _, transition = self._advance(
            engine_paths,
            runs_repo,
            "adv-paused",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=_Prompter([FailurePromptDecision.CONTINUE], on_prompt=read_row),
        )

        assert seen == {"status": RunStatus.PAUSED, "leaf_state": NodeState.PAUSED, "result_status": "failed"}
        assert transition == NodeTransitionKind.CONTINUED
        row = runs_repo.get("adv-paused")
        assert row is not None
        assert row.status == RunStatus.RUNNING

    def test_keyboard_interrupt_at_prompt_returns_paused_with_state_persisted(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: a prompter raising KeyboardInterrupt returns NodeTransitionKind.PAUSED, and the row is paused with the leaf PAUSED."""
        _, transition = self._advance(
            engine_paths,
            runs_repo,
            "adv-interrupt",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=_Prompter(interrupt=True),
        )

        row = runs_repo.get("adv-interrupt")
        assert row is not None
        assert transition == NodeTransitionKind.PAUSED
        assert row.status == RunStatus.PAUSED
        assert _leaf(_state(engine_paths, runs_repo, "adv-interrupt"), "a").state == NodeState.PAUSED

    def test_keyboard_interrupt_at_top_level_prompt_returns_paused_outcome_with_diagnostic(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a KeyboardInterrupt raised by the prompter of a top-level leaf returns a PAUSED outcome with the failed-step diagnostic as errors[0]."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "adv-paused-outcome",
            [_step("a", "exit 1", on_failure="prompt_user"), _step("b", "echo b")],
            prompter=_Prompter(interrupt=True),
        )

        assert outcome.status == RunStatus.PAUSED
        assert outcome.errors[0] == f"Step 'a' failed: {_FAIL_DETAIL}"
        assert outcome.step_results == []

    def test_retry_exhausted_escalates_to_on_max_retries_prompt(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: retry max_retries 2 with on_max_retries prompt_user prompts once after both attempts fail, with the prompted result's attempts == 2."""
        prompter = _Prompter([FailurePromptDecision.ABORT])

        _, transition = self._advance(
            engine_paths,
            runs_repo,
            "adv-exhausted",
            [
                _step(
                    "a",
                    "exit 1",
                    on_failure={"action": "retry", "max_retries": 2, "backoff_ms": 0, "on_max_retries": "prompt_user"},
                )
            ],
            prompter=prompter,
        )

        assert transition == NodeTransitionKind.FAILED
        assert [result.attempts for result in prompter.prompted] == [2]


def _counting_body(paused_position: int | None) -> list[dict[str, object]]:
    """Three prompt_user body steps that append to <id>.count; the step at paused_position also fails until `allow` exists."""
    body: list[dict[str, object]] = []
    for position in range(3):
        command = f"echo ran >> s{position}.count"
        if position == paused_position:
            command += "; test -f allow"
        body.append(_step(f"s{position}", command, on_failure="prompt_user"))
    return body


def _runs(workspace: Path, step_id: str) -> int:
    counter = workspace / f"{step_id}.count"
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.exists() else 0


def _pause_loop(
    paths: WorkspacePaths,
    runs: RunsRepository,
    session_id: str,
    steps: list[dict[str, object]],
) -> RunOutcome:
    """Seed a run and execute it with a prompter that Ctrl-Cs at the first failure prompt, pausing the run."""
    return _run_new(paths, runs, session_id, steps, prompter=_Prompter(interrupt=True))


class CoordinatorLoopBodyParityTests:
    """[tier-1/integration] RunCoordinator loop bodies: body leaves share the top-level leaf path."""

    @pytest.mark.parametrize(
        ("on_failure", "decisions", "no_tty"),
        [
            pytest.param({"action": "retry", "max_retries": 1, "backoff_ms": 0}, [], False, id="retry-then-pass"),
            pytest.param("continue", [], False, id="continue"),
            pytest.param("prompt_user", [FailurePromptDecision.CONTINUE], False, id="prompt-continue"),
            pytest.param("prompt_user", [FailurePromptDecision.ABORT], False, id="prompt-abort"),
            pytest.param("prompt_user", [], True, id="prompt-no-tty"),
        ],
    )
    def test_loop_body_leaf_matches_top_level_leaf_under_same_failure_policy(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        on_failure: str | dict[str, object],
        decisions: list[FailurePromptDecision],
        no_tty: bool,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: for on_failure retry-then-pass, continue, prompt_user answered continue, prompt_user answered abort, and prompt_user with no_tty, a single-step loop (max_iterations 1) yields the same (status, attempts, error_message) for its body leaf and the same outcome errors and warnings as the identical top-level step."""

        def flaky(marker: str) -> dict[str, object]:
            command = f"test -f {marker} || {{ touch {marker}; exit 1; }}"
            return _step("s", command, on_failure=on_failure)

        top = _run_new(
            engine_paths, runs_repo, "top", [flaky("top.marker")], prompter=_Prompter(decisions), no_tty=no_tty
        )
        looped = _run_new(
            engine_paths,
            runs_repo,
            "looped",
            [_loop("loop", [flaky("loop.marker")], max_iterations=1, until=["iteration.index >= 1"])],
            prompter=_Prompter(decisions),
            no_tty=no_tty,
        )

        top_leaf = _leaf(_state(engine_paths, runs_repo, "top"), "s")
        body_leaf = _loop_node(_state(engine_paths, runs_repo, "looped")).iterations[0].steps[0]
        assert looped.status == top.status
        assert (body_leaf.state, [a.number for a in body_leaf.attempts]) == (
            top_leaf.state,
            [a.number for a in top_leaf.attempts],
        )
        assert _last_result(body_leaf).error_message == _last_result(top_leaf).error_message
        assert looped.errors == top.errors
        assert looped.warnings == top.warnings

    def test_loop_body_step_declaring_artifacts_publishes_them(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a loop body step with an `artifacts` entry that succeeds publishes exactly one artifact row named per the spec."""
        _run_new(
            engine_paths,
            runs_repo,
            "loop-arts",
            [
                _loop(
                    "loop",
                    [_step("s", "echo hi > out.txt", artifacts=[{"name": "out", "path": "out.txt"}])],
                    max_iterations=1,
                )
            ],
            artifacts_db=artifacts_repository,
        )

        assert [record.name for record in artifacts_repository.list(session_id="loop-arts")] == ["out"]

    def test_second_iteration_step_sees_first_iteration_step_as_previous_step(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: iteration 2's first body step echoing {{ previous_step.status }} prints "completed" from iteration 1's last body step."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-previous",
            [_loop("loop", [_step("s", "echo PREV={{ previous_step.status }}")])],
        )

        assert [result.stdout for result in outcome.step_results] == ["PREV=\n", "PREV=completed\n"]

    def test_iteration_index_and_attempt_logs_are_distinct_per_iteration(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a two-iteration loop writes attempt logs whose names contain `_iter_1_` and `_iter_2_` and neither overwrites the other."""
        _run_new(engine_paths, runs_repo, "loop-logs", [_loop("loop", [_step("s", "echo $WT_ITERATION_INDEX")])])

        names = sorted(path.name for path in (engine_paths.logs_dir / "loop-logs").glob("*.stdout.log"))
        assert names == ["01_s_iter_1_attempt_1.stdout.log", "01_s_iter_2_attempt_1.stdout.log"]


class CoordinatorLoopResumeTests:
    """[tier-1/integration] RunCoordinator loop resume: the paused iteration and body step continue from durable state."""

    @pytest.mark.parametrize("paused_position", [0, 1, 2])
    def test_interrupt_at_body_step_pauses_run_with_loop_and_iteration_running(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, paused_position: int
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: Ctrl-C from the prompter at body step `paused_position` of a three-step body returns PAUSED; that leaf is paused with a failed last attempt, earlier leaves completed, later leaves pending, iteration 1 and the loop running."""
        outcome = _pause_loop(
            engine_paths,
            runs_repo,
            "loop-pause",
            [_loop("loop", _counting_body(paused_position), max_iterations=1)],
        )

        assert outcome.status == RunStatus.PAUSED
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-pause"))
        iteration = loop.iterations[0]
        expected = [NodeState.COMPLETED] * paused_position + [NodeState.PAUSED]
        expected += [NodeState.PENDING] * (2 - paused_position)
        assert [leaf.state for leaf in iteration.steps] == expected
        assert _last_result(iteration.steps[paused_position]).status == "failed"
        assert (iteration.state, loop.state) == (NodeState.RUNNING, NodeState.RUNNING)

    @pytest.mark.parametrize("paused_position", [0, 1, 2])
    def test_resume_retry_skips_terminal_steps_and_reprompts_from_persisted_result(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        engine_workspace: Path,
        paused_position: int,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: resuming after a pause at `paused_position` with a retry decision leaves each earlier step's counter file at one line, hands the prompter the persisted failed result (exit_code 1, original error_message) before the paused command re-runs as attempt 2, runs later steps once, and returns COMPLETED."""
        steps = [_loop("loop", _counting_body(paused_position), max_iterations=1)]
        _pause_loop(engine_paths, runs_repo, "loop-retry", steps)
        paused_id = f"s{paused_position}"
        runs_at_prompt: list[int] = []

        def allow_retry() -> None:
            runs_at_prompt.append(_runs(engine_workspace, paused_id))
            (engine_workspace / "allow").touch()

        prompter = _Prompter([FailurePromptDecision.RETRY], on_prompt=allow_retry)

        outcome = _coordinator(engine_paths, runs_repo, "loop-retry", prompter=prompter).execute()

        assert outcome.status == RunStatus.COMPLETED
        assert [(r.exit_code, r.error_message) for r in prompter.prompted] == [(1, _FAIL_DETAIL)]
        assert runs_at_prompt == [1]
        assert [_runs(engine_workspace, f"s{position}") for position in range(3)] == [
            2 if position == paused_position else 1 for position in range(3)
        ]
        leaf = _loop_node(_state(engine_paths, runs_repo, "loop-retry")).iterations[0].steps[paused_position]
        assert [attempt.number for attempt in leaf.attempts] == [1, 2]

    def test_resume_continue_ignores_paused_step_and_proceeds(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: resume with continue marks the paused body leaf ignored with the user-continued marker, runs the remaining body steps, and returns COMPLETED."""
        _pause_loop(engine_paths, runs_repo, "loop-continue", [_loop("loop", _counting_body(1), max_iterations=1)])

        outcome = _coordinator(
            engine_paths, runs_repo, "loop-continue", prompter=_Prompter([FailurePromptDecision.CONTINUE])
        ).execute()

        assert outcome.status == RunStatus.COMPLETED
        paused = _loop_node(_state(engine_paths, runs_repo, "loop-continue")).iterations[0].steps[1]
        assert paused.state == NodeState.IGNORED
        assert USER_CONTINUED_MARKER in (_last_result(paused).error_message or "")
        assert _runs(engine_workspace, "s2") == 1

    def test_resume_abort_fails_loop_and_leaves_later_steps_pending(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: resume with abort returns FAILED with errors == ["Step '<id>' failed: <detail>"], the body leaf, iteration, and loop failed, later body leaves pending, and no further iteration."""
        _pause_loop(engine_paths, runs_repo, "loop-abort-resume", [_loop("loop", _counting_body(1))])

        outcome = _coordinator(
            engine_paths, runs_repo, "loop-abort-resume", prompter=_Prompter([FailurePromptDecision.ABORT])
        ).execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Step 's1' failed: {_FAIL_DETAIL}"]
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-abort-resume"))
        assert len(loop.iterations) == 1
        assert [leaf.state for leaf in loop.iterations[0].steps] == [
            NodeState.COMPLETED,
            NodeState.FAILED,
            NodeState.PENDING,
        ]
        assert (loop.iterations[0].state, loop.state) == (NodeState.FAILED, NodeState.FAILED)

    def test_pause_in_second_iteration_never_reexecutes_first_iteration(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a pause at body step 2 of iteration 2 then resume keeps every iteration-1 step's counter file at one line and finishes iteration 2 without creating a third iteration."""
        second_iteration_gate = "echo ran >> s1.count; test $(wc -l < s1.count) -lt 2 || test -f allow"
        steps = [
            _loop(
                "loop",
                [_step("s0", "echo ran >> s0.count"), _step("s1", second_iteration_gate, on_failure="prompt_user")],
            )
        ]
        _pause_loop(engine_paths, runs_repo, "loop-second", steps)
        prompter = _Prompter([FailurePromptDecision.RETRY], on_prompt=lambda: (engine_workspace / "allow").touch())

        outcome = _coordinator(engine_paths, runs_repo, "loop-second", prompter=prompter).execute()

        assert outcome.status == RunStatus.COMPLETED
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-second"))
        assert [len(leaf.attempts) for leaf in loop.iterations[0].steps] == [1, 1]
        assert [leaf.state for leaf in loop.iterations[1].steps] == [NodeState.COMPLETED, NodeState.COMPLETED]
        assert len(loop.iterations) == 2
        assert [_runs(engine_workspace, "s0"), _runs(engine_workspace, "s1")] == [2, 3]

    def test_paused_and_resumed_loop_matches_uninterrupted_flattened_results(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: the flattened (step_id, status) list of a paused-then-resumed two-iteration loop equals the uninterrupted run's list, in the same order."""
        gate = "test $(wc -l < s1.count) -lt 2 || test -f allow"
        steps = [
            _loop(
                "loop",
                [_step("s0", "true"), _step("s1", f"echo ran >> s1.count; {gate}", on_failure="prompt_user")],
            )
        ]
        (engine_workspace / "allow").touch()
        straight = _run_new(engine_paths, runs_repo, "straight-loop", steps)
        (engine_workspace / "allow").unlink()
        (engine_workspace / "s1.count").unlink()
        _pause_loop(engine_paths, runs_repo, "resumed-loop", steps)
        prompter = _Prompter([FailurePromptDecision.RETRY], on_prompt=lambda: (engine_workspace / "allow").touch())

        resumed = _coordinator(engine_paths, runs_repo, "resumed-loop", prompter=prompter).execute()

        assert [(r.step_id, r.status) for r in resumed.step_results] == [
            (r.step_id, r.status) for r in straight.step_results
        ]

    def test_running_body_attempt_without_result_recovers_to_paused_and_reprompts(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a body leaf RUNNING with an unfinished attempt is closed as failed with "Step attempt was interrupted before it recorded a result.", marked paused, handed to the prompter, and its command is not re-run."""
        seed_new_run(
            engine_paths,
            runs_repo,
            session_id="loop-crashed",
            steps=[_loop("loop", [_step("s", "touch s.ran", on_failure="prompt_user")], max_iterations=1)],
        )
        state = _state(engine_paths, runs_repo, "loop-crashed")
        loop = _loop_node(state)
        loop.state = loop.iterations[0].state = NodeState.RUNNING
        leaf = loop.iterations[0].steps[0]
        leaf.state = NodeState.RUNNING
        leaf.attempts = [StepAttemptRecord(number=1, started_at="t")]
        assert RunStateStore(runs_repo, engine_paths, "loop-crashed").save(state).ok
        prompter = _Prompter([FailurePromptDecision.CONTINUE])

        outcome = _coordinator(engine_paths, runs_repo, "loop-crashed", prompter=prompter).execute()

        assert outcome.status == RunStatus.COMPLETED
        assert [r.error_message for r in prompter.prompted] == [
            "Step attempt was interrupted before it recorded a result."
        ]
        assert not (engine_workspace / "s.ran").exists()

    def test_resume_emits_no_second_loop_start_or_iteration_start_for_the_paused_iteration(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: run.log after pause plus resume holds exactly one LOOP_START and one LOOP_ITERATION_START for iteration 1."""
        _pause_loop(engine_paths, runs_repo, "loop-events", [_loop("loop", _counting_body(1), max_iterations=1)])

        _coordinator(
            engine_paths, runs_repo, "loop-events", prompter=_Prompter([FailurePromptDecision.CONTINUE])
        ).execute()

        events = [event["event"] for event in _run_log(engine_paths, "loop-events")]
        assert events.count("loop_start") == 1
        assert events.count("loop_iteration_start") == 1


class CoordinatorLoopUntilTests:
    """[tier-1/integration] RunCoordinator loop conditions: until sees the whole iteration."""

    def test_until_after_resume_sees_results_persisted_before_the_pause(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: body [a, b] with until ["steps.a.exit_code == 0", "steps.b.exit_code == 0"], paused at b and resumed with retry, completes in iteration 1 with until_passed True and an observer on_loop_conditions_evaluated carrying two passed results."""
        steps = [
            _loop(
                "loop",
                [_step("a", "true"), _step("b", "test -f allow", on_failure="prompt_user")],
                max_iterations=3,
                until=["steps.a.exit_code == 0", "steps.b.exit_code == 0"],
            )
        ]
        _pause_loop(engine_paths, runs_repo, "loop-until", steps)
        observer = _SequenceObserver()
        prompter = _Prompter([FailurePromptDecision.RETRY], on_prompt=lambda: (engine_workspace / "allow").touch())

        outcome = _coordinator(engine_paths, runs_repo, "loop-until", prompter=prompter, observer=observer).execute()

        assert outcome.status == RunStatus.COMPLETED
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-until"))
        assert [(it.number, it.until_passed) for it in loop.iterations] == [(1, True)]
        assert ("loop_conditions_evaluated", "loop", [True, True], True, None) in observer.calls


class CoordinatorLoopTerminalTests:
    """[tier-1/integration] RunCoordinator loop transitions: iteration completion, repetition, and loop termination."""

    def test_passing_until_on_first_iteration_persists_single_completed_iteration(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: max_iterations 5 with until true on iteration 1 persists exactly one iteration (COMPLETED, until_passed True) and a COMPLETED loop."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-pass",
            [_loop("loop", [_step("s", "true")], max_iterations=5, until=["steps.s.exit_code == 0"])],
        )

        assert outcome.status == RunStatus.COMPLETED
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-pass"))
        assert [(it.number, it.state, it.until_passed) for it in loop.iterations] == [(1, NodeState.COMPLETED, True)]
        assert loop.state == NodeState.COMPLETED

    def test_failed_until_persists_completed_iteration_before_next_iteration_exists(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        saved_states: list[ExecutionStateTree],
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a saved revision exists with iteration 1 COMPLETED and until_passed False and only one iteration in the loop, and a later revision adds iteration 2."""
        _run_new(engine_paths, runs_repo, "loop-persist", [_loop("loop", [_step("s", "true")])])

        shapes = [
            [(it.number, it.state, it.until_passed) for it in _loop_node(state).iterations] for state in saved_states
        ]
        completed_first = shapes.index([(1, NodeState.COMPLETED, False)])
        assert any(len(shape) == 2 for shape in shapes[completed_first + 1 :])

    def test_loop_block_results_recorded_as_iterations_and_flattened_in_order(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: plain step, two-iteration loop of two steps, plain step returns step_results ids [p1, s1, s2, s1, s2, p2] with iterations numbered [1, 2] and until_passed [False, True]."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-ok",
            [
                _step("p1", "echo p1"),
                _loop("loop", [_step("s1", "echo s1"), _step("s2", "echo s2")]),
                _step("p2", "echo p2"),
            ],
        )

        assert [result.step_id for result in outcome.step_results] == ["p1", "s1", "s2", "s1", "s2", "p2"]
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-ok"))
        assert loop.state == NodeState.COMPLETED
        assert [(it.number, it.until_passed) for it in loop.iterations] == [(1, False), (2, True)]
        assert [[leaf.state for leaf in iteration.steps] for iteration in loop.iterations] == [
            [NodeState.COMPLETED, NodeState.COMPLETED],
            [NodeState.COMPLETED, NodeState.COMPLETED],
        ]

    def test_body_abort_marks_iteration_and_loop_failed_with_single_step_error(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a body step failing under abort returns FAILED with errors == ["Step 's' failed: <detail>"] (once), iteration FAILED, loop FAILED."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-abort",
            [_loop("loop", [_step("s", "exit 1")], max_iterations=1)],
        )

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Step 's' failed: {_FAIL_DETAIL}"]
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-abort"))
        assert (loop.iterations[0].state, loop.state) == (NodeState.FAILED, NodeState.FAILED)


class CoordinatorLoopCeilingTests:
    """[tier-1/integration] RunCoordinator loop ceiling: on_max_iterations applies exactly once."""

    _CEILING = "Loop 'loop' reached max_iterations (2) without meeting 'until' conditions."

    @pytest.mark.parametrize(
        ("policy", "status", "errors", "warnings", "loop_state"),
        [
            pytest.param("abort", RunStatus.FAILED, [_CEILING], [], NodeState.FAILED, id="abort"),
            pytest.param(
                "continue",
                RunStatus.COMPLETED,
                [],
                [_CEILING.removesuffix(".") + "; continuing."],
                NodeState.COMPLETED,
                id="continue",
            ),
        ],
    )
    def test_ceiling_policy_applies_once(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        policy: str,
        status: RunStatus,
        errors: list[str],
        warnings: list[str],
        loop_state: NodeState,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: max_iterations 2 with unmet until: abort -> FAILED, errors == ["Loop 'loop' reached max_iterations (2) without meeting 'until' conditions."], loop FAILED; continue -> COMPLETED with that text ending "; continuing." once in warnings, loop COMPLETED; each has iterations with until_passed [False, False]."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-ceiling",
            [_loop("loop", [_step("s", "true")], until=["iteration.index >= 99"], on_max_iterations=policy)],
        )

        assert (outcome.status, outcome.errors, outcome.warnings) == (status, errors, warnings)
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-ceiling"))
        assert loop.state == loop_state
        assert [it.until_passed for it in loop.iterations] == [False, False]

    @pytest.mark.parametrize(
        ("answer", "no_tty", "status", "errors", "warnings"),
        [
            pytest.param(
                LoopPromptDecision.CONTINUE,
                False,
                RunStatus.COMPLETED,
                [],
                [_CEILING.removesuffix(".") + "; continuing."],
                id="continue",
            ),
            pytest.param(
                LoopPromptDecision.ABORT,
                False,
                RunStatus.FAILED,
                ["Loop 'loop' aborted by user after max_iterations."],
                [],
                id="abort",
            ),
            pytest.param(
                None,
                True,
                RunStatus.FAILED,
                ["Loop 'loop' reached max_iterations (2) and run is non-interactive."],
                [],
                id="no-tty",
            ),
        ],
    )
    def test_prompt_user_ceiling_continue_abort_and_non_interactive(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        answer: LoopPromptDecision | None,
        no_tty: bool,
        status: RunStatus,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: prompt_user ceiling answered continue -> COMPLETED with the continuing warning; answered abort -> FAILED with "Loop 'loop' aborted by user after max_iterations."; no_tty -> FAILED with "Loop 'loop' reached max_iterations (2) and run is non-interactive." and the prompter never consulted."""
        prompter = _Prompter(loop_decisions=[answer] if answer is not None else [])

        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-prompt",
            [_loop("loop", [_step("s", "true")], until=["iteration.index >= 99"])],
            prompter=prompter,
            no_tty=no_tty,
        )

        assert (outcome.status, outcome.errors, outcome.warnings) == (status, errors, warnings)
        assert prompter.loop_prompts == ([] if no_tty else [(2, 3)])

    def test_grant_decision_persists_larger_ceiling_and_runs_more_iterations(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: max_iterations 1, until "iteration.index >= 3", prompter answering grant once: prompter called once with iteration 1 and grant_count 3, persisted loop.max_iterations 1 and granted_iterations 3, three iterations, COMPLETED."""
        prompter = _Prompter(loop_decisions=[LoopPromptDecision.GRANT])

        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-grant",
            [_loop("loop", [_step("s", "true")], max_iterations=1, until=["iteration.index >= 3"])],
            prompter=prompter,
        )

        assert outcome.status == RunStatus.COMPLETED
        assert prompter.loop_prompts == [(1, 3)]
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-grant"))
        assert (loop.max_iterations, loop.granted_iterations, len(loop.iterations)) == (1, 3, 3)

    def test_interrupt_at_ceiling_prompt_cancels_run_and_marks_loop_cancelled(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: Ctrl-C from the ceiling prompt returns CANCELLED, the loop node is cancelled, and the last body leaf stays completed."""
        outcome = _run_new(
            engine_paths,
            runs_repo,
            "loop-ceiling-interrupt",
            [_loop("loop", [_step("s", "true")], max_iterations=1, until=["iteration.index >= 99"])],
            prompter=_Prompter(interrupt_loop=True),
        )

        assert outcome.status == RunStatus.CANCELLED
        loop = _loop_node(_state(engine_paths, runs_repo, "loop-ceiling-interrupt"))
        assert (loop.state, loop.iterations[-1].steps[-1].state) == (NodeState.CANCELLED, NodeState.COMPLETED)


class CoordinatorCorruptLoopStateTests:
    """[tier-1/integration] RunCoordinator.execute: loop state that differs structurally from the snapshot is rejected before any step runs."""

    @pytest.mark.parametrize("corruption", ["loop_id", "child_order", "child_id"])
    def test_structurally_corrupt_loop_state_fails_before_any_step_runs(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        engine_workspace: Path,
        corruption: str,
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a saved tree with a renamed loop id, swapped iteration children, or a renamed child returns FAILED with errors == ["Execution state for run '<id>' is corrupt: <validator message>"], no marker file created, and no further state revision saved."""
        body = [_step("edit", "touch edit.ran"), _step("verify", "touch verify.ran")]
        seed_new_run(engine_paths, runs_repo, session_id="corrupt-loop", steps=[_loop("fix", body)])
        store = RunStateStore(runs_repo, engine_paths, "corrupt-loop")
        state = _state(engine_paths, runs_repo, "corrupt-loop")
        loop = _loop_node(state, "fix")
        if corruption == "loop_id":
            loop.id = "ghost"
            expected = "Loop 'ghost' does not match a loop in the run snapshot."
        elif corruption == "child_order":
            loop.iterations[0].steps.reverse()
            expected = "Loop 'fix' iteration 1 steps ['verify', 'edit'] do not match the loop body ['edit', 'verify']."
        else:
            loop.iterations[0].steps[1].id = "renamed"
            expected = "Loop 'fix' iteration 1 steps ['edit', 'renamed'] do not match the loop body ['edit', 'verify']."
        assert store.save(state).ok
        revision = _state(engine_paths, runs_repo, "corrupt-loop").revision

        outcome = _coordinator(engine_paths, runs_repo, "corrupt-loop").execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Execution state for run 'corrupt-loop' is corrupt: {expected}"]
        assert not (engine_workspace / "edit.ran").exists()
        assert _state(engine_paths, runs_repo, "corrupt-loop").revision == revision


class CoordinatorPausedGateTests:
    """[tier-1/integration] RunCoordinator.execute: a PAUSED leaf re-enters the failure gate without re-executing."""

    _STEPS: ClassVar[list[dict[str, object]]] = [
        _step("a", "true"),
        _step("b", "echo ran >> b.ran", on_failure="prompt_user"),
        _step("c", "touch c.ran"),
    ]

    def _seed(self, paths: WorkspacePaths, runs: RunsRepository, session_id: str) -> None:
        seed_paused_run(paths, runs, session_id=session_id, steps=self._STEPS, paused_step_id="b")

    def test_paused_leaf_reprompts_from_last_attempt_without_reexecuting(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a PAUSED leaf resumed with a prompter answering continue never runs its command (counter file absent), passes the persisted failed result (exit_code 1, error_message from the seed) to the prompter, and ends ignored."""
        self._seed(engine_paths, runs_repo, "gate-continue")
        prompter = _Prompter([FailurePromptDecision.CONTINUE])

        outcome = _coordinator(engine_paths, runs_repo, "gate-continue", prompter=prompter).execute()

        assert not (engine_workspace / "b.ran").exists()
        assert [(r.exit_code, r.error_message) for r in prompter.prompted] == [(1, SEEDED_FAILURE)]
        assert _leaf(_state(engine_paths, runs_repo, "gate-continue"), "b").state == NodeState.IGNORED
        assert [(r.step_id, r.status) for r in outcome.step_results] == [
            ("a", "completed"),
            ("b", "ignored"),
            ("c", "completed"),
        ]

    def test_paused_leaf_retry_decision_starts_next_attempt_and_reexecutes_once(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a PAUSED leaf whose last attempt is number 1 resumed with retry runs its command exactly once as attempt 2, and on success the node is completed with attempts numbered [1, 2]."""
        self._seed(engine_paths, runs_repo, "gate-retry")

        outcome = _coordinator(
            engine_paths, runs_repo, "gate-retry", prompter=_Prompter([FailurePromptDecision.RETRY])
        ).execute()

        assert outcome.status == RunStatus.COMPLETED
        assert (engine_workspace / "b.ran").read_text(encoding="utf-8") == "ran\n"
        leaf = _leaf(_state(engine_paths, runs_repo, "gate-retry"), "b")
        assert leaf.state == NodeState.COMPLETED
        assert [attempt.number for attempt in leaf.attempts] == [1, 2]

    def test_paused_leaf_abort_decision_fails_node_without_executing(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a PAUSED leaf resumed with abort returns FAILED with errors == ["Step '<id>' failed: <detail>"], node failed, command marker file absent, later leaves pending."""
        self._seed(engine_paths, runs_repo, "gate-abort")

        outcome = _coordinator(
            engine_paths, runs_repo, "gate-abort", prompter=_Prompter([FailurePromptDecision.ABORT])
        ).execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [f"Step 'b' failed: {SEEDED_FAILURE}"]
        state = _state(engine_paths, runs_repo, "gate-abort")
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.FAILED, NodeState.PENDING]
        assert not (engine_workspace / "b.ran").exists()
        assert not (engine_workspace / "c.ran").exists()


class CoordinatorInvalidStateTests:
    """[tier-1/integration] RunCoordinator: runs whose durable state cannot be driven fail without executing steps."""

    def test_step_missing_from_snapshot_definitions_fails_run_without_executing(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a state node whose id has no snapshot definition returns FAILED with errors == ["Step 'ghost' has no matching definition in the run snapshot."] and no marker file."""
        seed_new_run(engine_paths, runs_repo, session_id="ghost-def", steps=[_step("a", "touch a.ran")])
        store = RunStateStore(runs_repo, engine_paths, "ghost-def")
        state = _state(engine_paths, runs_repo, "ghost-def")
        _leaf(state, "a").id = "ghost"
        assert store.save(state).ok

        outcome = _coordinator(engine_paths, runs_repo, "ghost-def").execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Step 'ghost' has no matching definition in the run snapshot."]
        assert not (engine_workspace / "a.ran").exists()

    def test_paused_leaf_without_recorded_result_fails_run_without_executing(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a PAUSED leaf whose last attempt has no result returns FAILED with errors == ["Paused step 'b' has no recorded attempt result."] and no marker file."""
        seed_paused_run(
            engine_paths,
            runs_repo,
            session_id="no-result",
            steps=[_step("a", "true"), _step("b", "touch b.ran")],
            paused_step_id="b",
        )
        state = _state(engine_paths, runs_repo, "no-result")
        leaf = _leaf(state, "b")
        leaf.attempts[-1] = leaf.attempts[-1].model_copy(update={"result": None})
        assert RunStateStore(runs_repo, engine_paths, "no-result").save(state).ok

        outcome = _coordinator(engine_paths, runs_repo, "no-result").execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Paused step 'b' has no recorded attempt result."]
        assert not (engine_workspace / "b.ran").exists()

    def test_run_row_deleted_after_state_load_fails_run(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: a run row that disappears after its state was loaded returns FAILED with errors == ["Run 'vanished' not found."]."""
        seed_new_run(engine_paths, runs_repo, session_id="vanished", steps=[_step("a", "true")])
        real_get = RunsRepository.get
        calls: list[str] = []

        def get_once(self: RunsRepository, session_id: str):
            calls.append(session_id)
            return real_get(self, session_id) if len(calls) == 1 else None

        monkeypatch.setattr(RunsRepository, "get", get_once)

        outcome = _coordinator(engine_paths, runs_repo, "vanished").execute()

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == ["Run 'vanished' not found."]

    def test_advance_leaf_for_node_outside_the_tree_fails_without_executing(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_leaf: a leaf whose id is not in the state tree returns FAILED with errors naming the step and no marker file."""
        seed_new_run(engine_paths, runs_repo, session_id="outside", steps=[_step("a", "touch a.ran")])
        state = _state(engine_paths, runs_repo, "outside")
        definition = load_blueprint_from_snapshot(engine_paths.session_dir("outside"), state.manifest).steps[0]
        assert isinstance(definition, StepDefinition)
        coordinator = _coordinator(engine_paths, runs_repo, "outside")

        transition = coordinator.advance_leaf(ExecutionLeafNode(id="ghost"), definition)

        assert transition == NodeTransitionKind.FAILED
        assert not (engine_workspace / "a.ran").exists()

    def test_advance_loop_for_node_outside_the_tree_fails_without_executing(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, engine_workspace: Path
    ) -> None:
        """[tier-1/integration] RunCoordinator.advance_loop: a loop whose id is not in the state tree returns FAILED and runs no body step."""
        body = [_step("edit", "touch edit.ran")]
        seed_new_run(engine_paths, runs_repo, session_id="outside-loop", steps=[_loop("fix", body)])
        state = _state(engine_paths, runs_repo, "outside-loop")
        definition = load_blueprint_from_snapshot(engine_paths.session_dir("outside-loop"), state.manifest).steps[0]
        assert isinstance(definition, LoopStepBlock)
        coordinator = _coordinator(engine_paths, runs_repo, "outside-loop")

        transition = coordinator.advance_loop(ExecutionLoopNode(id="ghost", max_iterations=1), definition)

        assert transition == NodeTransitionKind.FAILED
        assert not (engine_workspace / "edit.ran").exists()


class CoordinatorSandboxIdTests:
    """[tier-1/integration] RunCoordinator sandbox identity: the row's sandbox_id follows the session's sandbox."""

    def test_sandboxed_run_persists_sandbox_id_on_first_transition_save(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: with context.sandbox set, the row read from inside the first step's command already has sandbox_id equal to context.sandbox.session_id."""
        sandbox = SandboxSession(
            session_id="sbx-77",
            target_branch="worktree/sandbox-sbx-77",
            sandbox_path=engine_paths.root_dir,
            base_commit="abc",
            created_at="now",
        )
        observed: list[str | None] = []

        def read_row_from_step(self: StepExecution) -> StepResult:
            row = runs_repo.get("sbx-run")
            assert row is not None
            observed.append(row.sandbox_id)
            return StepResult(step_id="a", status="completed", exit_code=0, stdout="", stderr="", duration_seconds=0.0)

        monkeypatch.setattr(StepExecution, "run", read_row_from_step)
        seed_new_run(engine_paths, runs_repo, session_id="sbx-run", steps=[_step("a", "true")])

        outcome = _coordinator(engine_paths, runs_repo, "sbx-run", sandbox=sandbox).execute()

        assert observed == ["sbx-77"]
        assert outcome.sandbox_id == "sbx-77"

    def test_run_without_sandbox_leaves_sandbox_id_none(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] RunCoordinator.execute: with context.sandbox None the completed row has sandbox_id None."""
        outcome = _run_new(engine_paths, runs_repo, "no-sbx", [_step("a", "true")])

        row = runs_repo.get("no-sbx")
        assert row is not None
        assert row.sandbox_id is None
        assert outcome.sandbox_id is None
