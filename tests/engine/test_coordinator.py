"""Contract tests for RunCoordinator: durable leaf transitions, prompt recovery, loops, and worktree identity."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.services.download import download_artifact
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.interaction import InteractionAnswer, InteractionRequest, RequestOption
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.core.sessions.services.read_logs import read_session_log_events
from dovo.core.worktree.models import WorktreeSession
from dovo.engine import RunCoordinator, SessionStateStore
from dovo.engine.executors import StepExecution
from dovo.engine.executors.models import StepResult
from dovo.engine.failure import USER_CONTINUED_MARKER
from dovo.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    InteractionPrompter,
    LoopPromptDecision,
    RunContext,
    RunObserver,
    RunOutcome,
)
from dovo.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from dovo.engine.writer import snapshot_blueprint_path
from tests.engine.conftest import SpySaveExecutionState
from tests.harness.sessions import (
    SEEDED_FAILURE,
    NoOpRunObserver,
    RecordingRunObserver,
    ScriptedFailurePrompter,
    seed_new_session,
    seed_paused_session,
)

_FAIL_DETAIL = "Command failed with exit code 1."


def _timeline_entry(event: SessionLogEvent) -> tuple[str, dict[str, object]]:
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
    worktree: WorktreeSession | None = None,
    no_tty: bool = False,
    artifacts_db: ArtifactsRepository | None = None,
    interaction_prompter: InteractionPrompter | None = None,
) -> RunContext:
    """Session context with scratch and log directories created like drive_run does."""
    session_tmp_dir = paths.tmp_dir / session_id
    (session_tmp_dir / "steps").mkdir(parents=True, exist_ok=True)
    session_log_dir = paths.logs_dir / session_id
    session_log_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(
        session_id=session_id,
        paths=paths,
        target_dir=paths.root_dir,
        session_tmp_dir=session_tmp_dir,
        session_log_dir=session_log_dir,
        artifact_store=ArtifactStore(Artifacts(paths, artifacts_db), session_id) if artifacts_db is not None else None,
        worktree=worktree,
        no_tty=no_tty,
        interaction_prompter=interaction_prompter,
    )


def _coordinator(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    session_id: str,
    *,
    prompter: FailurePrompter | None = None,
    observer: RunObserver | None = None,
    worktree: WorktreeSession | None = None,
    no_tty: bool = False,
    artifacts_db: ArtifactsRepository | None = None,
) -> RunCoordinator:
    return RunCoordinator(
        SessionStateStore(sessions, paths, session_id),
        _context(paths, session_id, worktree=worktree, no_tty=no_tty, artifacts_db=artifacts_db),
        observer=observer,
        prompter=prompter,
    )


def _run_new(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    session_id: str,
    steps: list[dict[str, object]],
    *,
    prompter: FailurePrompter | None = None,
    observer: RunObserver | None = None,
    no_tty: bool = False,
    artifacts_db: ArtifactsRepository | None = None,
) -> RunOutcome:
    """Seed a fresh run and execute it through RunCoordinator."""
    seed_new_session(paths, sessions, session_id=session_id, steps=steps)
    return _coordinator(
        paths, sessions, session_id, prompter=prompter, observer=observer, no_tty=no_tty, artifacts_db=artifacts_db
    ).execute()


def _state(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> ExecutionStateTree:
    state = SessionStateStore(sessions, paths, session_id).load().state
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


def _session_log(paths: WorkspacePaths, session_id: str) -> list[dict[str, object]]:
    lines = (paths.logs_dir / session_id / "session.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _last_result(node: ExecutionLeafNode) -> StepResult:
    result = node.attempts[-1].result
    assert result is not None
    return result


class CoordinatorExecuteTests:
    def test_two_leaf_run_completes_with_flattened_results_and_advancing_revision(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(engine_paths, sessions_repo, "two", [_step("a", "echo a"), _step("b", "echo b")])

        assert outcome.status == SessionStatus.COMPLETED
        assert [result.step_id for result in outcome.step_results] == ["a", "b"]
        state = _state(engine_paths, sessions_repo, "two")
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.COMPLETED]
        row = sessions_repo.get("two")
        assert row is not None
        assert (row.execution_state_revision or 0) > 0

    def test_abort_policy_failure_marks_leaf_failed_and_skips_later_leaves(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        outcome = _run_new(engine_paths, sessions_repo, "abort", [_step("a", "exit 1"), _step("b", "touch b.ran")])

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {_FAIL_DETAIL}"]
        state = _state(engine_paths, sessions_repo, "abort")
        assert [node.state for node in state.nodes] == [NodeState.FAILED, NodeState.PENDING]
        assert not (engine_workspace / "b.ran").exists()

    def test_continue_policy_marks_leaf_ignored_and_runs_remaining_leaves(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "cont",
            [_step("a", "exit 1", on_failure="continue"), _step("b", "echo b")],
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert [result.status for result in outcome.step_results] == ["ignored", "completed"]
        assert _leaf(_state(engine_paths, sessions_repo, "cont"), "a").state == NodeState.IGNORED

    def test_fresh_and_resumed_linear_runs_produce_equal_step_results(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        straight = _run_new(
            engine_paths,
            sessions_repo,
            "straight",
            [_step("a", "true"), _step("b", "exit 1", on_failure="continue"), _step("c", "true")],
        )
        seed_paused_session(
            engine_paths,
            sessions_repo,
            session_id="resumed",
            steps=[_step("a", "true"), _step("b", "exit 1", on_failure="prompt_user"), _step("c", "true")],
            paused_step_id="b",
        )

        resumed = _coordinator(
            engine_paths, sessions_repo, "resumed", prompter=ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])
        ).execute()

        assert [(r.step_id, r.status) for r in resumed.step_results] == [
            (r.step_id, r.status) for r in straight.step_results
        ]
        assert [r.status for r in resumed.step_results] == ["completed", "ignored", "completed"]

    def test_unreadable_state_returns_failed_outcome_without_running_steps(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="bad", steps=[_step("a", "touch a.ran")])
        sessions_repo.save_execution_state("bad", "{not json", expected_revision=0, next_revision=1)

        outcome = _coordinator(engine_paths, sessions_repo, "bad").execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Execution state for session 'bad' is corrupt."]
        assert not (engine_workspace / "a.ran").exists()

    def test_persistence_failure_after_step_result_stops_dispatch_and_names_boundary(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        engine_workspace: Path,
        spy_save_execution_state: SpySaveExecutionState,
    ) -> None:
        seed_new_session(
            engine_paths, sessions_repo, session_id="persist", steps=[_step("a", "echo a"), _step("b", "touch b.ran")]
        )
        spy_save_execution_state(lambda saves: len(saves) != 2)

        outcome = _coordinator(engine_paths, sessions_repo, "persist").execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors[0].startswith("Failed to persist run state after step 'a' finished:")
        assert not (engine_workspace / "b.ran").exists()

    def test_interrupted_attempt_is_closed_failed_and_never_completed(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="crash", steps=[_step("a", "touch a.ran")])
        store = SessionStateStore(sessions_repo, engine_paths, "crash")
        state = _state(engine_paths, sessions_repo, "crash")
        interrupted = _leaf(state, "a")
        interrupted.state = NodeState.RUNNING
        interrupted.attempts = [StepAttemptRecord(number=1, started_at="then")]
        assert store.save(state).ok

        outcome = _coordinator(engine_paths, sessions_repo, "crash").execute()

        leaf = _leaf(_state(engine_paths, sessions_repo, "crash"), "a")
        assert leaf.state == NodeState.FAILED
        assert _last_result(leaf).status == "failed"
        assert _last_result(leaf).error_message == "Step attempt was interrupted before it recorded a result."
        assert outcome.status == SessionStatus.FAILED
        assert not (engine_workspace / "a.ran").exists()

    def test_keyboard_interrupt_during_step_cancels_run_and_marks_node_cancelled(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        def interrupted(self: StepExecution) -> StepResult:
            raise KeyboardInterrupt

        monkeypatch.setattr(StepExecution, "run", interrupted)

        outcome = _run_new(engine_paths, sessions_repo, "ctrl-c", [_step("a", "echo a"), _step("b", "echo b")])

        assert outcome.status == SessionStatus.CANCELLED
        assert outcome.errors == ["Execution cancelled by user."]
        state = _state(engine_paths, sessions_repo, "ctrl-c")
        assert [node.state for node in state.nodes] == [NodeState.CANCELLED, NodeState.PENDING]

    def test_observer_exception_does_not_abort_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "raising",
            [_step("a", "echo a"), _step("b", "echo b")],
            observer=_RaisingObserver(),
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert [result.step_id for result in outcome.step_results] == ["a", "b"]

    def test_retried_step_logs_final_attempt_number(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        flaky = 'n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count; [ "$n" -ge 3 ]'
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "retry",
            [_step("a", flaky, on_failure={"action": "retry", "max_retries": 3, "backoff_ms": 0})],
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert outcome.step_results[0].attempts == 3
        session_log = (engine_paths.logs_dir / "retry" / "session.log").read_text(encoding="utf-8")
        done = [json.loads(line) for line in session_log.splitlines() if '"step_done"' in line]
        assert [event["attempt"] for event in done] == [3]

    @pytest.mark.parametrize(
        ("command", "expected_artifacts"),
        [
            pytest.param("echo hi > out.txt", ["out"], id="passing-publishes"),
            pytest.param("echo hi > out.txt; exit 1", [], id="failing-publishes-nothing"),
        ],
    )
    def test_completed_leaf_with_declared_artifacts_publishes_them_through_step_coordinator(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
        command: str,
        expected_artifacts: list[str],
    ) -> None:
        _run_new(
            engine_paths,
            sessions_repo,
            "arts",
            [_step("a", command, artifacts=[{"name": "out", "path": "out.txt"}])],
            artifacts_db=artifacts_repository,
        )

        assert [record.name for record in artifacts_repository.list(session_id="arts")] == expected_artifacts


class CoordinatorArtifactRevisionsTests:
    def test_loop_republishing_plan_creates_revisions_one_to_n_all_downloadable(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
        tmp_path: Path,
    ) -> None:
        steps = [
            _loop(
                "loop",
                [
                    _step(
                        "write-plan",
                        "echo plan-{{ iteration.index }} > PLAN.md",
                        artifacts=[{"name": "plan", "path": "PLAN.md"}],
                    )
                ],
                max_iterations=3,
            )
        ]

        _run_new(engine_paths, sessions_repo, "loop-revisions", steps, artifacts_db=artifacts_repository)

        records = sorted(artifacts_repository.list(session_id="loop-revisions"), key=lambda record: record.revision)
        assert [(record.revision, record.loop_iteration) for record in records] == [(1, 1), (2, 2), (3, 3)]
        downloaded: list[str] = []
        for revision in (1, 2, 3):
            dest = tmp_path / f"download-{revision}"
            result = download_artifact(
                engine_paths.artifacts_dir,
                artifacts_repository,
                session_id="loop-revisions",
                name="plan",
                dest=dest,
                revision=revision,
            )
            assert result.ok
            downloaded.append((dest / "PLAN.md").read_text(encoding="utf-8"))
        assert downloaded == ["plan-1\n", "plan-2\n", "plan-3\n"]


class CoordinatorObserverContractTests:
    def test_linear_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        observer = RecordingRunObserver()

        _run_new(
            engine_paths,
            sessions_repo,
            "linear",
            [_step("a", "echo a"), _step("b", "echo b"), _step("c", "echo c")],
            observer=observer,
        )

        assert [call for call in observer.calls if call[0] != "step_output"] == [
            ("step_start", 1, 3, "a"),
            ("step_done", 1, 3, "a", "completed"),
            ("step_start", 2, 3, "b"),
            ("step_done", 2, 3, "b", "completed"),
            ("step_start", 3, 3, "c"),
            ("step_done", 3, 3, "c", "completed"),
        ]

    def test_loop_run_emits_pinned_observer_sequence(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        observer = RecordingRunObserver()

        _run_new(
            engine_paths,
            sessions_repo,
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

    def test_loop_session_log_round_trips_through_reader(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _run_new(engine_paths, sessions_repo, "loop-log", [_loop("loop", [_step("s1", "true"), _step("s2", "true")])])
        log_dir = engine_paths.logs_dir / "loop-log"

        events = read_session_log_events(log_dir, tail=None)

        assert len(events) == len((log_dir / "session.log").read_text(encoding="utf-8").splitlines())
        durations = [e.duration_seconds for e in events if e.event is SessionLogEventType.STEP_DONE]
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
        evaluated = [e for e in events if e.event is SessionLogEventType.LOOP_CONDITIONS_EVALUATED]
        assert [[c["passed"] for c in e.conditions or []] for e in evaluated] == [[False], [True]]

    def test_top_level_step_events_leave_loop_fields_null(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _run_new(engine_paths, sessions_repo, "flat-log", [_step("a", "true"), _step("b", "true")])

        events = read_session_log_events(engine_paths.logs_dir / "flat-log", tail=None)

        step_events = [e for e in events if e.event.value in {"step_start", "step_done"}]
        assert len(step_events) == 4
        assert [(e.loop_id, e.iteration) for e in step_events] == [(None, None)] * 4

    def test_step_name_and_duration_are_logged(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "named-log",
            [_step("a", "true", name="Run tests"), _step("b", "true")],
        )

        events = read_session_log_events(engine_paths.logs_dir / "named-log", tail=None)

        assert [e.step_name for e in events if e.event.value == "step_start"] == ["Run tests", None]
        assert [e.duration_seconds for e in events if e.event.value == "step_done"] == [
            result.duration_seconds for result in outcome.step_results
        ]


class CoordinatorLoadTests:
    def test_execute_after_failed_load_returns_failed_with_load_error_once(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="load-fail", steps=[_step("a", "true")])
        snapshot_blueprint_path(engine_paths.session_dir("load-fail"), "load-fail").unlink()
        coordinator = _coordinator(engine_paths, sessions_repo, "load-fail")

        assert coordinator.load() is False
        outcome = coordinator.execute()

        assert outcome.status == SessionStatus.FAILED
        assert len(outcome.errors) == 1

    def test_steps_returns_top_level_definitions_after_load(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seed_new_session(
            engine_paths,
            sessions_repo,
            session_id="load-steps",
            steps=[_step("a", "true"), _loop("loop", [_step("s1", "true")])],
        )
        coordinator = _coordinator(engine_paths, sessions_repo, "load-steps")

        assert coordinator.load() is True
        snapshot_blueprint_path(engine_paths.session_dir("load-steps"), "load-steps").unlink()

        assert [step.id for step in coordinator.steps] == ["a", "loop"]
        assert coordinator.load() is True


class CoordinatorMetadataTests:
    def test_first_step_sees_empty_previous_step_env_vars(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-first",
            [_step("first_step", 'echo "PREV_ID=[$DOVO_PREVIOUS_STEP_ID] PREV_STATUS=[$DOVO_PREVIOUS_STEP_STATUS]"')],
        )

        assert outcome.step_results[0].stdout == "PREV_ID=[] PREV_STATUS=[]\n"

    def test_second_step_sees_previous_step_id_name_index_status_exit_code_env_vars(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-prev",
            [
                _step("setup_step", "echo 'setup done'", name="Setup Step"),
                _step(
                    "verify_step",
                    'echo "PREV_ID=$DOVO_PREVIOUS_STEP_ID PREV_NAME=$DOVO_PREVIOUS_STEP_NAME '
                    "PREV_IDX=$DOVO_PREVIOUS_STEP_INDEX PREV_STATUS=$DOVO_PREVIOUS_STEP_STATUS "
                    'PREV_EXIT=$DOVO_PREVIOUS_STEP_EXIT_CODE"',
                ),
            ],
        )

        assert (
            outcome.step_results[1].stdout
            == "PREV_ID=setup_step PREV_NAME=Setup Step PREV_IDX=1 PREV_STATUS=completed PREV_EXIT=0\n"
        )

    def test_continue_on_failure_previous_step_status_is_ignored_with_exit_code_zero(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-continue",
            [
                _step("failing_step", "exit 3", on_failure="continue"),
                _step(
                    "next_step",
                    'echo "PREV_ID=$DOVO_PREVIOUS_STEP_ID PREV_STATUS=$DOVO_PREVIOUS_STEP_STATUS '
                    'PREV_EXIT=$DOVO_PREVIOUS_STEP_EXIT_CODE"',
                ),
            ],
        )

        assert outcome.step_results[0].error_message == "Command failed with exit code 3."
        assert outcome.step_results[1].stdout == "PREV_ID=failing_step PREV_STATUS=ignored PREV_EXIT=0\n"

    def test_prompt_user_retry_increments_step_attempt_env_var_from_one_to_two(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-retry",
            [
                _step(
                    "retry_on_prompt",
                    'if [ "$DOVO_STEP_ATTEMPT" -eq 1 ]; then echo fail1 >&2; exit 1; else echo success2; fi',
                    on_failure="prompt_user",
                )
            ],
            prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY]),
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert (outcome.step_results[0].stdout, outcome.step_results[0].attempts) == ("success2\n", 2)

    def test_three_step_run_propagates_steps_context_and_dovo_steps_json_excluding_in_flight_step(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-json",
            [
                _step("step_a", 'echo "A_STEPS=[{{ steps[0].id }}] A_JSON=$DOVO_STEPS_JSON"', name="Step Alpha"),
                _step(
                    "step_b",
                    'echo "B_LAST={{ steps[-1].id }} B_A_STAT={{ steps.step_a.status }} B_JSON=$DOVO_STEPS_JSON"',
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
                'echo "greeting=hello" >> "$DOVO_OUTPUT"',
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
        sessions_repo: SessionsRepository,
        producer: str,
        consumer: str,
        expected: str,
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-outputs",
            [_step("step_a", producer), _step("step_b", f'echo "{consumer}"')],
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert outcome.step_results[1].stdout == expected

    def test_final_step_sees_correctly_named_steps_for_prior_step_and_both_loop_iterations(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "meta-loop",
            [
                _step("first", "echo first", name="First"),
                _loop("loop", [_step("tick", "echo tick", name="Tick")]),
                _step("last", 'echo "{{ steps[0].name }}|{{ steps[1].name }}|{{ steps[2].name }}"'),
            ],
        )

        assert len(outcome.step_results) == 4
        assert outcome.step_results[-1].stdout == "First|Tick|Tick\n"


class CoordinatorLeafTransitionTests:
    def test_pending_leaf_success_returns_completed_and_persists_state(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(engine_paths, sessions_repo, "adv-ok", [_step("a", "echo a")])

        leaf = _leaf(_state(engine_paths, sessions_repo, "adv-ok"), "a")
        assert outcome.status == SessionStatus.COMPLETED
        assert leaf.state == NodeState.COMPLETED
        assert [attempt.number for attempt in leaf.attempts] == [1]

    def test_prompt_retry_then_abort_prompts_twice_and_fails_node_with_two_attempts(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        prompter = ScriptedFailurePrompter([FailurePromptDecision.RETRY, FailurePromptDecision.ABORT])

        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "adv-retry",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=prompter,
        )

        leaf = _leaf(_state(engine_paths, sessions_repo, "adv-retry"), "a")
        assert outcome.status == SessionStatus.FAILED
        assert len(prompter.prompted) == 2
        assert leaf.state == NodeState.FAILED
        assert [attempt.number for attempt in leaf.attempts] == [1, 2]

    def test_prompt_continue_decision_ignores_node_with_user_continued_marker(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "adv-continue",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=ScriptedFailurePrompter([FailurePromptDecision.CONTINUE]),
        )

        leaf = _leaf(_state(engine_paths, sessions_repo, "adv-continue"), "a")
        assert outcome.status == SessionStatus.COMPLETED
        assert leaf.state == NodeState.IGNORED
        assert (_last_result(leaf).error_message or "").endswith("(user continued after prompt_user)")

    def test_prompt_abort_decision_fails_node_with_step_failed_message(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "adv-abort",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=ScriptedFailurePrompter([FailurePromptDecision.ABORT]),
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {_FAIL_DETAIL}"]
        assert _leaf(_state(engine_paths, sessions_repo, "adv-abort"), "a").state == NodeState.FAILED

    @pytest.mark.parametrize(
        ("no_tty", "prompter", "warning_substr"),
        [
            pytest.param(True, ScriptedFailurePrompter(), "non-interactive", id="no-tty"),
            pytest.param(False, None, "no failure prompter", id="no-prompter"),
        ],
    )
    def test_prompt_user_without_interactive_prompter_aborts_and_never_persists_paused(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        spy_save_execution_state: SpySaveExecutionState,
        no_tty: bool,
        prompter: ScriptedFailurePrompter | None,
        warning_substr: str,
    ) -> None:
        saves = spy_save_execution_state(None)

        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "adv-noninteractive",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=prompter,
            no_tty=no_tty,
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 'a' failed: {_FAIL_DETAIL}"]
        assert len(outcome.warnings) == 1
        assert outcome.warnings[0].startswith("Warning: step 'a' requested prompt_user but")
        assert warning_substr in outcome.warnings[0]
        assert SessionStatus.PAUSED not in [save.status for save in saves]
        assert prompter is None or prompter.prompted == []

    def test_prompt_persists_paused_state_before_prompter_is_consulted(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        seen: dict[str, object] = {}

        def read_row() -> None:
            row = sessions_repo.get("adv-paused")
            assert row is not None
            leaf = _leaf(_state(engine_paths, sessions_repo, "adv-paused"), "a")
            seen["status"] = row.status
            seen["leaf_state"] = leaf.state
            seen["result_status"] = _last_result(leaf).status

        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "adv-paused",
            [_step("a", "exit 1", on_failure="prompt_user")],
            prompter=ScriptedFailurePrompter([FailurePromptDecision.CONTINUE], on_prompt=read_row),
        )

        assert seen == {"status": SessionStatus.PAUSED, "leaf_state": NodeState.PAUSED, "result_status": "failed"}
        assert outcome.status == SessionStatus.COMPLETED

    def test_keyboard_interrupt_at_top_level_prompt_returns_paused_outcome_with_diagnostic(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "adv-paused-outcome",
            [_step("a", "exit 1", on_failure="prompt_user"), _step("b", "echo b")],
            prompter=ScriptedFailurePrompter([KeyboardInterrupt()]),
        )

        assert outcome.status == SessionStatus.PAUSED
        row = sessions_repo.get("adv-paused-outcome")
        assert row is not None
        assert outcome.errors[0] == f"Step 'a' failed: {_FAIL_DETAIL}"
        assert outcome.step_results == []
        assert row.status == SessionStatus.PAUSED
        assert _leaf(_state(engine_paths, sessions_repo, "adv-paused-outcome"), "a").state == NodeState.PAUSED

    def test_retry_exhausted_escalates_to_on_max_retries_prompt(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        prompter = ScriptedFailurePrompter([FailurePromptDecision.ABORT])

        outcome = _run_new(
            engine_paths,
            sessions_repo,
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

        assert outcome.status == SessionStatus.FAILED
        assert [result.attempts for result in prompter.prompted] == [2]


def _counting_body(paused_position: int | None) -> list[dict[str, object]]:
    """Build three prompt_user body steps counting runs in <id>.count; paused_position fails until `allow` exists."""
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
    sessions: SessionsRepository,
    session_id: str,
    steps: list[dict[str, object]],
) -> RunOutcome:
    """Seed a run and execute it with a prompter that Ctrl-Cs at the first failure prompt, pausing the run."""
    return _run_new(paths, sessions, session_id, steps, prompter=ScriptedFailurePrompter([KeyboardInterrupt()]))


class CoordinatorLoopBodyParityTests:
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
        sessions_repo: SessionsRepository,
        on_failure: str | dict[str, object],
        decisions: list[FailurePromptDecision],
        no_tty: bool,
    ) -> None:

        def flaky(marker: str) -> dict[str, object]:
            command = f"test -f {marker} || {{ touch {marker}; exit 1; }}"
            return _step("s", command, on_failure=on_failure)

        top = _run_new(
            engine_paths,
            sessions_repo,
            "top",
            [flaky("top.marker")],
            prompter=ScriptedFailurePrompter(decisions),
            no_tty=no_tty,
        )
        looped = _run_new(
            engine_paths,
            sessions_repo,
            "looped",
            [_loop("loop", [flaky("loop.marker")], max_iterations=1, until=["iteration.index >= 1"])],
            prompter=ScriptedFailurePrompter(decisions),
            no_tty=no_tty,
        )

        top_leaf = _leaf(_state(engine_paths, sessions_repo, "top"), "s")
        body_leaf = _loop_node(_state(engine_paths, sessions_repo, "looped")).iterations[0].steps[0]
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
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        _run_new(
            engine_paths,
            sessions_repo,
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
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-previous",
            [_loop("loop", [_step("s", "echo PREV={{ previous_step.status }}")])],
        )

        assert [result.stdout for result in outcome.step_results] == ["PREV=\n", "PREV=completed\n"]

    def test_iteration_index_and_attempt_logs_are_distinct_per_iteration(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _run_new(engine_paths, sessions_repo, "loop-logs", [_loop("loop", [_step("s", "echo $DOVO_ITERATION_INDEX")])])

        names = sorted(path.name for path in (engine_paths.logs_dir / "loop-logs").glob("*.stdout.log"))
        assert names == ["01_s_iter_1_attempt_1.stdout.log", "01_s_iter_2_attempt_1.stdout.log"]


class CoordinatorLoopResumeTests:
    @pytest.mark.parametrize("paused_position", [0, 1, 2])
    def test_interrupt_at_body_step_pauses_run_with_loop_and_iteration_running(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, paused_position: int
    ) -> None:
        outcome = _pause_loop(
            engine_paths,
            sessions_repo,
            "loop-pause",
            [_loop("loop", _counting_body(paused_position), max_iterations=1)],
        )

        assert outcome.status == SessionStatus.PAUSED
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-pause"))
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
        sessions_repo: SessionsRepository,
        engine_workspace: Path,
        paused_position: int,
    ) -> None:
        steps = [_loop("loop", _counting_body(paused_position), max_iterations=1)]
        _pause_loop(engine_paths, sessions_repo, "loop-retry", steps)
        paused_id = f"s{paused_position}"
        runs_at_prompt: list[int] = []

        def allow_retry() -> None:
            runs_at_prompt.append(_runs(engine_workspace, paused_id))
            (engine_workspace / "allow").touch()

        prompter = ScriptedFailurePrompter([FailurePromptDecision.RETRY], on_prompt=allow_retry)

        outcome = _coordinator(engine_paths, sessions_repo, "loop-retry", prompter=prompter).execute()

        assert outcome.status == SessionStatus.COMPLETED
        assert [(r.exit_code, r.error_message) for r in prompter.prompted] == [(1, _FAIL_DETAIL)]
        assert runs_at_prompt == [1]
        assert [_runs(engine_workspace, f"s{position}") for position in range(3)] == [
            2 if position == paused_position else 1 for position in range(3)
        ]
        leaf = _loop_node(_state(engine_paths, sessions_repo, "loop-retry")).iterations[0].steps[paused_position]
        assert [attempt.number for attempt in leaf.attempts] == [1, 2]

    def test_resume_continue_ignores_paused_step_and_proceeds(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        _pause_loop(engine_paths, sessions_repo, "loop-continue", [_loop("loop", _counting_body(1), max_iterations=1)])

        outcome = _coordinator(
            engine_paths,
            sessions_repo,
            "loop-continue",
            prompter=ScriptedFailurePrompter([FailurePromptDecision.CONTINUE]),
        ).execute()

        assert outcome.status == SessionStatus.COMPLETED
        paused = _loop_node(_state(engine_paths, sessions_repo, "loop-continue")).iterations[0].steps[1]
        assert paused.state == NodeState.IGNORED
        assert USER_CONTINUED_MARKER in (_last_result(paused).error_message or "")
        assert _runs(engine_workspace, "s2") == 1

    def test_resume_abort_fails_loop_and_leaves_later_steps_pending(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _pause_loop(engine_paths, sessions_repo, "loop-abort-resume", [_loop("loop", _counting_body(1))])

        outcome = _coordinator(
            engine_paths,
            sessions_repo,
            "loop-abort-resume",
            prompter=ScriptedFailurePrompter([FailurePromptDecision.ABORT]),
        ).execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 's1' failed: {_FAIL_DETAIL}"]
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-abort-resume"))
        assert len(loop.iterations) == 1
        assert [leaf.state for leaf in loop.iterations[0].steps] == [
            NodeState.COMPLETED,
            NodeState.FAILED,
            NodeState.PENDING,
        ]
        assert (loop.iterations[0].state, loop.state) == (NodeState.FAILED, NodeState.FAILED)

    def test_pause_in_second_iteration_never_reexecutes_first_iteration(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        second_iteration_gate = "echo ran >> s1.count; test $(wc -l < s1.count) -lt 2 || test -f allow"
        steps = [
            _loop(
                "loop",
                [_step("s0", "echo ran >> s0.count"), _step("s1", second_iteration_gate, on_failure="prompt_user")],
            )
        ]
        _pause_loop(engine_paths, sessions_repo, "loop-second", steps)
        prompter = ScriptedFailurePrompter(
            [FailurePromptDecision.RETRY], on_prompt=lambda: (engine_workspace / "allow").touch()
        )

        outcome = _coordinator(engine_paths, sessions_repo, "loop-second", prompter=prompter).execute()

        assert outcome.status == SessionStatus.COMPLETED
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-second"))
        assert [len(leaf.attempts) for leaf in loop.iterations[0].steps] == [1, 1]
        assert [leaf.state for leaf in loop.iterations[1].steps] == [NodeState.COMPLETED, NodeState.COMPLETED]
        assert len(loop.iterations) == 2
        assert [_runs(engine_workspace, "s0"), _runs(engine_workspace, "s1")] == [2, 3]

    def test_paused_and_resumed_loop_matches_uninterrupted_flattened_results(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        gate = "test $(wc -l < s1.count) -lt 2 || test -f allow"
        steps = [
            _loop(
                "loop",
                [_step("s0", "true"), _step("s1", f"echo ran >> s1.count; {gate}", on_failure="prompt_user")],
            )
        ]
        (engine_workspace / "allow").touch()
        straight = _run_new(engine_paths, sessions_repo, "straight-loop", steps)
        (engine_workspace / "allow").unlink()
        (engine_workspace / "s1.count").unlink()
        _pause_loop(engine_paths, sessions_repo, "resumed-loop", steps)
        prompter = ScriptedFailurePrompter(
            [FailurePromptDecision.RETRY], on_prompt=lambda: (engine_workspace / "allow").touch()
        )

        resumed = _coordinator(engine_paths, sessions_repo, "resumed-loop", prompter=prompter).execute()

        assert [(r.step_id, r.status) for r in resumed.step_results] == [
            (r.step_id, r.status) for r in straight.step_results
        ]

    def test_running_body_attempt_without_result_recovers_to_paused_and_reprompts(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        seed_new_session(
            engine_paths,
            sessions_repo,
            session_id="loop-crashed",
            steps=[_loop("loop", [_step("s", "touch s.ran", on_failure="prompt_user")], max_iterations=1)],
        )
        state = _state(engine_paths, sessions_repo, "loop-crashed")
        loop = _loop_node(state)
        loop.state = loop.iterations[0].state = NodeState.RUNNING
        leaf = loop.iterations[0].steps[0]
        leaf.state = NodeState.RUNNING
        leaf.attempts = [StepAttemptRecord(number=1, started_at="t")]
        assert SessionStateStore(sessions_repo, engine_paths, "loop-crashed").save(state).ok
        prompter = ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])

        outcome = _coordinator(engine_paths, sessions_repo, "loop-crashed", prompter=prompter).execute()

        assert outcome.status == SessionStatus.COMPLETED
        assert [r.error_message for r in prompter.prompted] == [
            "Step attempt was interrupted before it recorded a result."
        ]
        assert not (engine_workspace / "s.ran").exists()

    def test_resume_emits_no_second_loop_start_or_iteration_start_for_the_paused_iteration(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _pause_loop(engine_paths, sessions_repo, "loop-events", [_loop("loop", _counting_body(1), max_iterations=1)])

        _coordinator(
            engine_paths,
            sessions_repo,
            "loop-events",
            prompter=ScriptedFailurePrompter([FailurePromptDecision.CONTINUE]),
        ).execute()

        events = [event["event"] for event in _session_log(engine_paths, "loop-events")]
        assert events.count("loop_start") == 1
        assert events.count("loop_iteration_start") == 1


class CoordinatorLoopUntilTests:
    def test_until_after_resume_sees_results_persisted_before_the_pause(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps = [
            _loop(
                "loop",
                [_step("a", "true"), _step("b", "test -f allow", on_failure="prompt_user")],
                max_iterations=3,
                until=["steps.a.exit_code == 0", "steps.b.exit_code == 0"],
            )
        ]
        _pause_loop(engine_paths, sessions_repo, "loop-until", steps)
        observer = RecordingRunObserver()
        prompter = ScriptedFailurePrompter(
            [FailurePromptDecision.RETRY], on_prompt=lambda: (engine_workspace / "allow").touch()
        )

        outcome = _coordinator(
            engine_paths, sessions_repo, "loop-until", prompter=prompter, observer=observer
        ).execute()

        assert outcome.status == SessionStatus.COMPLETED
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-until"))
        assert [(it.number, it.until_passed) for it in loop.iterations] == [(1, True)]
        assert ("loop_conditions_evaluated", "loop", [True, True], True, None) in observer.calls


class CoordinatorLoopTerminalTests:
    def test_passing_until_on_first_iteration_persists_single_completed_iteration(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-pass",
            [_loop("loop", [_step("s", "true")], max_iterations=5, until=["steps.s.exit_code == 0"])],
        )

        assert outcome.status == SessionStatus.COMPLETED
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-pass"))
        assert [(it.number, it.state, it.until_passed) for it in loop.iterations] == [(1, NodeState.COMPLETED, True)]
        assert loop.state == NodeState.COMPLETED

    def test_failed_until_persists_completed_iteration_before_next_iteration_exists(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        spy_save_execution_state: SpySaveExecutionState,
    ) -> None:
        """Persist iteration 1 COMPLETED with until_passed False before iteration 2 exists."""
        saves = spy_save_execution_state(None)

        _run_new(engine_paths, sessions_repo, "loop-persist", [_loop("loop", [_step("s", "true")])])

        shapes = [
            [(it.number, it.state, it.until_passed) for it in _loop_node(save.state).iterations] for save in saves
        ]
        completed_first = shapes.index([(1, NodeState.COMPLETED, False)])
        assert any(len(shape) == 2 for shape in shapes[completed_first + 1 :])

    def test_loop_block_results_recorded_as_iterations_and_flattened_in_order(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-ok",
            [
                _step("p1", "echo p1"),
                _loop("loop", [_step("s1", "echo s1"), _step("s2", "echo s2")]),
                _step("p2", "echo p2"),
            ],
        )

        assert [result.step_id for result in outcome.step_results] == ["p1", "s1", "s2", "s1", "s2", "p2"]
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-ok"))
        assert loop.state == NodeState.COMPLETED
        assert [(it.number, it.until_passed) for it in loop.iterations] == [(1, False), (2, True)]
        assert [[leaf.state for leaf in iteration.steps] for iteration in loop.iterations] == [
            [NodeState.COMPLETED, NodeState.COMPLETED],
            [NodeState.COMPLETED, NodeState.COMPLETED],
        ]

    def test_body_abort_marks_iteration_and_loop_failed_with_single_step_error(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-abort",
            [_loop("loop", [_step("s", "exit 1")], max_iterations=1)],
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 's' failed: {_FAIL_DETAIL}"]
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-abort"))
        assert (loop.iterations[0].state, loop.state) == (NodeState.FAILED, NodeState.FAILED)


class CoordinatorLoopCeilingTests:
    _CEILING = "Loop 'loop' reached max_iterations (2) without meeting 'until' conditions."

    @pytest.mark.parametrize(
        ("policy", "status", "errors", "warnings", "loop_state"),
        [
            pytest.param("abort", SessionStatus.FAILED, [_CEILING], [], NodeState.FAILED, id="abort"),
            pytest.param(
                "continue",
                SessionStatus.COMPLETED,
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
        sessions_repo: SessionsRepository,
        policy: str,
        status: SessionStatus,
        errors: list[str],
        warnings: list[str],
        loop_state: NodeState,
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-ceiling",
            [_loop("loop", [_step("s", "true")], until=["iteration.index >= 99"], on_max_iterations=policy)],
        )

        assert (outcome.status, outcome.errors, outcome.warnings) == (status, errors, warnings)
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-ceiling"))
        assert loop.state == loop_state
        assert [it.until_passed for it in loop.iterations] == [False, False]

    @pytest.mark.parametrize(
        ("answer", "no_tty", "status", "errors", "warnings"),
        [
            pytest.param(
                LoopPromptDecision.CONTINUE,
                False,
                SessionStatus.COMPLETED,
                [],
                [_CEILING.removesuffix(".") + "; continuing."],
                id="continue",
            ),
            pytest.param(
                LoopPromptDecision.ABORT,
                False,
                SessionStatus.FAILED,
                ["Loop 'loop' aborted by user after max_iterations."],
                [],
                id="abort",
            ),
            pytest.param(
                None,
                True,
                SessionStatus.FAILED,
                ["Loop 'loop' reached max_iterations (2) and run is non-interactive."],
                [],
                id="no-tty",
            ),
        ],
    )
    def test_prompt_user_ceiling_continue_abort_and_non_interactive(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        answer: LoopPromptDecision | None,
        no_tty: bool,
        status: SessionStatus,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        prompter = ScriptedFailurePrompter([answer] if answer is not None else [])

        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-prompt",
            [_loop("loop", [_step("s", "true")], until=["iteration.index >= 99"])],
            prompter=prompter,
            no_tty=no_tty,
        )

        assert (outcome.status, outcome.errors, outcome.warnings) == (status, errors, warnings)
        assert prompter.loop_prompts == ([] if no_tty else [(2, 3)])

    def test_grant_decision_persists_larger_ceiling_and_runs_more_iterations(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        prompter = ScriptedFailurePrompter([LoopPromptDecision.GRANT])

        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-grant",
            [_loop("loop", [_step("s", "true")], max_iterations=1, until=["iteration.index >= 3"])],
            prompter=prompter,
        )

        assert outcome.status == SessionStatus.COMPLETED
        assert prompter.loop_prompts == [(1, 3)]
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-grant"))
        assert (loop.max_iterations, loop.granted_iterations, len(loop.iterations)) == (1, 3, 3)

    def test_interrupt_at_ceiling_prompt_cancels_run_and_marks_loop_cancelled(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(
            engine_paths,
            sessions_repo,
            "loop-ceiling-interrupt",
            [_loop("loop", [_step("s", "true")], max_iterations=1, until=["iteration.index >= 99"])],
            prompter=ScriptedFailurePrompter([KeyboardInterrupt()]),
        )

        assert outcome.status == SessionStatus.CANCELLED
        loop = _loop_node(_state(engine_paths, sessions_repo, "loop-ceiling-interrupt"))
        assert (loop.state, loop.iterations[-1].steps[-1].state) == (NodeState.CANCELLED, NodeState.COMPLETED)


class CoordinatorCorruptLoopStateTests:
    @pytest.mark.parametrize("corruption", ["loop_id", "child_order", "child_id"])
    def test_structurally_corrupt_loop_state_fails_before_any_step_runs(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        engine_workspace: Path,
        corruption: str,
    ) -> None:
        body = [_step("edit", "touch edit.ran"), _step("verify", "touch verify.ran")]
        seed_new_session(engine_paths, sessions_repo, session_id="corrupt-loop", steps=[_loop("fix", body)])
        store = SessionStateStore(sessions_repo, engine_paths, "corrupt-loop")
        state = _state(engine_paths, sessions_repo, "corrupt-loop")
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
        revision = _state(engine_paths, sessions_repo, "corrupt-loop").revision

        outcome = _coordinator(engine_paths, sessions_repo, "corrupt-loop").execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Execution state for session 'corrupt-loop' is corrupt: {expected}"]
        assert not (engine_workspace / "edit.ran").exists()
        assert _state(engine_paths, sessions_repo, "corrupt-loop").revision == revision


class CoordinatorPausedGateTests:
    _STEPS: ClassVar[list[dict[str, object]]] = [
        _step("a", "true"),
        _step("b", "echo ran >> b.ran", on_failure="prompt_user"),
        _step("c", "touch c.ran"),
    ]

    def _seed(self, paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
        seed_paused_session(paths, sessions, session_id=session_id, steps=self._STEPS, paused_step_id="b")

    def test_paused_leaf_reprompts_from_last_attempt_without_reexecuting(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        self._seed(engine_paths, sessions_repo, "gate-continue")
        prompter = ScriptedFailurePrompter([FailurePromptDecision.CONTINUE])

        outcome = _coordinator(engine_paths, sessions_repo, "gate-continue", prompter=prompter).execute()

        assert not (engine_workspace / "b.ran").exists()
        assert [(r.exit_code, r.error_message) for r in prompter.prompted] == [(1, SEEDED_FAILURE)]
        assert _leaf(_state(engine_paths, sessions_repo, "gate-continue"), "b").state == NodeState.IGNORED
        assert [(r.step_id, r.status) for r in outcome.step_results] == [
            ("a", "completed"),
            ("b", "ignored"),
            ("c", "completed"),
        ]

    def test_paused_leaf_retry_decision_starts_next_attempt_and_reexecutes_once(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        self._seed(engine_paths, sessions_repo, "gate-retry")

        outcome = _coordinator(
            engine_paths, sessions_repo, "gate-retry", prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        ).execute()

        assert outcome.status == SessionStatus.COMPLETED
        assert (engine_workspace / "b.ran").read_text(encoding="utf-8") == "ran\n"
        leaf = _leaf(_state(engine_paths, sessions_repo, "gate-retry"), "b")
        assert leaf.state == NodeState.COMPLETED
        assert [attempt.number for attempt in leaf.attempts] == [1, 2]

    def test_paused_leaf_abort_decision_fails_node_without_executing(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        self._seed(engine_paths, sessions_repo, "gate-abort")

        outcome = _coordinator(
            engine_paths, sessions_repo, "gate-abort", prompter=ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        ).execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"Step 'b' failed: {SEEDED_FAILURE}"]
        state = _state(engine_paths, sessions_repo, "gate-abort")
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.FAILED, NodeState.PENDING]
        assert not (engine_workspace / "b.ran").exists()
        assert not (engine_workspace / "c.ran").exists()


class CoordinatorInvalidStateTests:
    def test_step_missing_from_snapshot_definitions_fails_run_without_executing(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="ghost-def", steps=[_step("a", "touch a.ran")])
        store = SessionStateStore(sessions_repo, engine_paths, "ghost-def")
        state = _state(engine_paths, sessions_repo, "ghost-def")
        _leaf(state, "a").id = "ghost"
        assert store.save(state).ok

        outcome = _coordinator(engine_paths, sessions_repo, "ghost-def").execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Step 'ghost' has no matching definition in the run snapshot."]
        assert not (engine_workspace / "a.ran").exists()

    def test_paused_leaf_without_recorded_result_fails_run_without_executing(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        seed_paused_session(
            engine_paths,
            sessions_repo,
            session_id="no-result",
            steps=[_step("a", "true"), _step("b", "touch b.ran")],
            paused_step_id="b",
        )
        state = _state(engine_paths, sessions_repo, "no-result")
        leaf = _leaf(state, "b")
        leaf.attempts[-1] = leaf.attempts[-1].model_copy(update={"result": None})
        assert SessionStateStore(sessions_repo, engine_paths, "no-result").save(state).ok

        outcome = _coordinator(engine_paths, sessions_repo, "no-result").execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Paused step 'b' has no recorded attempt result."]
        assert not (engine_workspace / "b.ran").exists()

    def test_run_row_deleted_after_state_load_fails_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_new_session(engine_paths, sessions_repo, session_id="vanished", steps=[_step("a", "true")])
        real_get = SessionsRepository.get
        calls: list[str] = []

        def get_once(self: SessionsRepository, session_id: str):
            calls.append(session_id)
            return real_get(self, session_id) if len(calls) == 1 else None

        monkeypatch.setattr(SessionsRepository, "get", get_once)

        outcome = _coordinator(engine_paths, sessions_repo, "vanished").execute()

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Session 'vanished' not found."]


class CoordinatorWorktreeIdTests:
    def test_worktree_run_persists_worktree_id_on_first_transition_save(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = WorktreeSession(
            session_id="dovo_77",
            target_branch="dovo/dovo_77",
            worktree_path=engine_paths.root_dir,
            base_commit="abc",
            created_at="now",
        )
        observed: list[str | None] = []

        def read_row_from_step(self: StepExecution) -> StepResult:
            row = sessions_repo.get("worktree-run")
            assert row is not None
            observed.append(row.worktree_id)
            return StepResult(step_id="a", status="completed", exit_code=0, stdout="", stderr="", duration_seconds=0.0)

        monkeypatch.setattr(StepExecution, "run", read_row_from_step)
        seed_new_session(engine_paths, sessions_repo, session_id="worktree-run", steps=[_step("a", "true")])

        outcome = _coordinator(engine_paths, sessions_repo, "worktree-run", worktree=worktree).execute()

        assert observed == ["dovo_77"]
        assert outcome.worktree_id == "dovo_77"

    def test_run_without_worktree_leaves_worktree_id_none(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        outcome = _run_new(engine_paths, sessions_repo, "no-worktree", [_step("a", "true")])

        row = sessions_repo.get("no-worktree")
        assert row is not None
        assert row.worktree_id is None
        assert outcome.worktree_id is None


_SESSION_ID = "ask_session"
_APPROVE_OPTIONS: list[dict[str, object]] = [
    {"value": "approve", "label": "Approve", "comment": "disabled"},
    {"value": "revise", "label": "Request changes", "comment": "required"},
    {"value": "deny", "label": "Deny", "fail": True},
]


def _approve_step(**extra: object) -> dict[str, object]:
    """Return a select ask step `approve` over approve / revise / deny."""
    interaction = {"mode": "select", "message": "Approve this plan?", "options": _APPROVE_OPTIONS}
    return {"id": "approve", "type": "ask", "interaction": interaction, **extra}


def _command(step_id: str, command: str, **extra: object) -> dict[str, object]:
    """Return a command step running command."""
    return {"id": step_id, "type": "command", "command": command, **extra}


def _triage_step(artifact: str) -> dict[str, object]:
    """Return a multiselect ask step `triage` whose options come from findings in artifact's review.json."""
    options_from = {
        "artifact": artifact,
        "file": "review.json",
        "items_path": "findings",
        "value_field": "id",
        "label_field": "summary",
    }
    interaction = {"mode": "multiselect", "message": "Fix?", "options_from": options_from}
    return {"id": "triage", "type": "ask", "interaction": interaction}


class _ScriptedPrompter(InteractionPrompter):
    """InteractionPrompter that replays scripted answers (None means unanswered) and records requests."""

    def __init__(
        self,
        answers: list[InteractionAnswer | BaseException | None],
        on_ask: Callable[[InteractionRequest], None] | None = None,
    ) -> None:
        self.answers = list(answers)
        self.requests: list[InteractionRequest] = []
        self.on_ask = on_ask

    def ask(self, request: InteractionRequest) -> InteractionAnswer | None:
        self.requests.append(request)
        if self.on_ask is not None:
            self.on_ask(request)
        scripted = self.answers.pop(0)
        if isinstance(scripted, BaseException):
            raise scripted
        return scripted


@dataclass
class AskRun:
    """Seed and drive ask runs through RunCoordinator over a real workspace."""

    paths: WorkspacePaths
    sessions: SessionsRepository
    artifacts: ArtifactsRepository

    def start(self, steps: list[dict[str, object]], prompter: InteractionPrompter | None = None) -> RunOutcome:
        """Seed a fresh session for steps and execute it."""
        seed_new_session(self.paths, self.sessions, session_id=_SESSION_ID, steps=steps)
        return self.execute(prompter)

    def execute(
        self, prompter: InteractionPrompter | None = None, failure_prompter: FailurePrompter | None = None
    ) -> RunOutcome:
        """Execute (or resume) the seeded session with a new coordinator."""
        context = _context(self.paths, _SESSION_ID, artifacts_db=self.artifacts, interaction_prompter=prompter)
        coordinator = RunCoordinator(
            SessionStateStore(self.sessions, self.paths, _SESSION_ID), context, prompter=failure_prompter
        )
        return coordinator.execute()

    def state(self) -> ExecutionStateTree:
        """Load the persisted execution state."""
        state = SessionStateStore(self.sessions, self.paths, _SESSION_ID).load().state
        assert state is not None
        return state

    def leaf(self, step_id: str) -> ExecutionLeafNode:
        """Return the persisted top-level leaf step_id."""
        node = next(node for node in self.state().nodes if node.id == step_id)
        assert isinstance(node, ExecutionLeafNode)
        return node

    def row_status(self) -> SessionStatus:
        """Return the persisted session row status."""
        row = self.sessions.get(_SESSION_ID)
        assert row is not None
        return row.status

    def file(self, name: str) -> Path:
        """Return a path in the workspace root, where steps run."""
        return self.paths.root_dir / name


@pytest.fixture
def ask_run(
    engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, artifacts_repository: ArtifactsRepository
) -> AskRun:
    """AskRun over the engine workspace."""
    return AskRun(engine_paths, sessions_repo, artifacts_repository)


def _answer(*selected: str, comment: str | None = None) -> InteractionAnswer:
    """Return an answer choosing selected."""
    return InteractionAnswer(selected=list(selected), comment=comment)


class CoordinatorAskTests:
    """Contract tests for ask-step transitions in RunCoordinator."""

    def test_pending_ask_is_persisted_paused_before_the_prompter_is_consulted(self, ask_run: AskRun) -> None:
        seen: list[tuple[NodeState, InteractionRequest | None, SessionStatus]] = []

        def inspect(request: InteractionRequest) -> None:
            leaf = ask_run.leaf("approve")
            seen.append((leaf.state, leaf.pending_ask, ask_run.row_status()))

        prompter = _ScriptedPrompter([_answer("approve")], on_ask=inspect)

        ask_run.start([_approve_step()], prompter)

        assert seen == [(NodeState.PAUSED, prompter.requests[0], SessionStatus.PAUSED)]

    def test_valid_answer_clears_pending_ask_and_completes_with_outputs(self, ask_run: AskRun) -> None:
        steps = [_approve_step(), _command("after", "echo '{{ steps.approve.outputs.selected }}' > got.txt")]

        outcome = ask_run.start(steps, _ScriptedPrompter([_answer("approve")]))

        leaf = ask_run.leaf("approve")
        assert outcome.status == SessionStatus.COMPLETED
        assert (leaf.state, leaf.pending_ask) == (NodeState.COMPLETED, None)
        assert leaf.attempts[-1].result is not None
        assert leaf.attempts[-1].result.outputs["selected"] == "approve"
        assert ask_run.row_status() == SessionStatus.RUNNING
        assert ask_run.file("got.txt").read_text(encoding="utf-8").strip() == "approve"

    @pytest.mark.parametrize(
        ("policy", "expected_status", "expected_state", "expected_errors"),
        [
            pytest.param(
                "abort",
                SessionStatus.FAILED,
                NodeState.FAILED,
                ["Step 'approve' failed: Prompt 'approve' answered 'deny'."],
                id="abort",
            ),
            pytest.param("continue", SessionStatus.COMPLETED, NodeState.IGNORED, [], id="continue"),
        ],
    )
    def test_fail_option_fails_the_step_and_applies_on_failure(
        self,
        ask_run: AskRun,
        policy: str,
        expected_status: SessionStatus,
        expected_state: NodeState,
        expected_errors: list[str],
    ) -> None:
        outcome = ask_run.start([_approve_step(on_failure=policy)], _ScriptedPrompter([_answer("deny")]))

        assert outcome.status == expected_status
        assert outcome.errors == expected_errors
        assert ask_run.leaf("approve").state == expected_state

    @pytest.mark.parametrize(
        "scripted",
        [
            pytest.param(None, id="no_prompter"),
            pytest.param([None], id="prompter_returns_none"),
            pytest.param([KeyboardInterrupt()], id="keyboard_interrupt"),
            pytest.param([_answer("maybe")], id="invalid_answer"),
        ],
    )
    def test_unanswered_prompt_leaves_run_paused_and_records_nothing(
        self, ask_run: AskRun, scripted: list[InteractionAnswer | BaseException | None] | None
    ) -> None:
        prompter = None if scripted is None else _ScriptedPrompter(scripted)

        outcome = ask_run.start([_approve_step()], prompter)

        leaf = ask_run.leaf("approve")
        assert (outcome.status, outcome.paused_prompt_id, outcome.errors) == (SessionStatus.PAUSED, "approve", [])
        assert (leaf.state, leaf.pending_ask is not None, leaf.attempts[-1].result) == (NodeState.PAUSED, True, None)

    def test_interrupt_between_choice_and_comment_records_nothing(self, ask_run: AskRun) -> None:
        ask_run.start([_approve_step()], None)
        pending = ask_run.leaf("approve")

        outcome = ask_run.execute(_ScriptedPrompter([KeyboardInterrupt()]))

        assert outcome.paused_prompt_id == "approve"
        assert ask_run.leaf("approve") == pending

    def test_resume_asks_the_stored_request_without_rerunning_earlier_steps(self, ask_run: AskRun) -> None:
        """Hand the prompter the stored request, even without its options artifact, without re-running the producer."""
        producer = _command(
            "review",
            'echo x >> counter.txt; echo \'{"findings": [{"id": "B1", "summary": "a"}]}\' > review.json',
            artifacts=[{"name": "review", "path": "review.json"}],
        )
        triage = _triage_step("review")
        ask_run.start([producer, triage], None)
        stored = ask_run.leaf("triage").pending_ask
        record = ask_run.artifacts.get(_SESSION_ID, "review")
        assert record is not None
        shutil.rmtree(record.path)
        prompter = _ScriptedPrompter([_answer("B1")])

        outcome = ask_run.execute(prompter)

        assert outcome.status == SessionStatus.COMPLETED
        assert prompter.requests == [stored]
        assert ask_run.file("counter.txt").read_text(encoding="utf-8").splitlines() == ["x"]

    def test_invalid_stored_pending_ask_fails_the_run(self, ask_run: AskRun) -> None:
        """Return FAILED with "Pending ask for 'approve' is invalid." and the leaf FAILED for a one-option select."""
        ask_run.start([_approve_step()], None)
        state = ask_run.state()
        leaf = state.nodes[0]
        assert isinstance(leaf, ExecutionLeafNode)
        assert leaf.pending_ask is not None
        leaf.pending_ask = leaf.pending_ask.model_copy(update={"options": [RequestOption(value="only", label="Only")]})
        assert SessionStateStore(ask_run.sessions, ask_run.paths, _SESSION_ID).save(state).ok

        outcome = ask_run.execute(_ScriptedPrompter([]))

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == ["Pending ask for 'approve' is invalid."]
        assert ask_run.leaf("approve").state == NodeState.FAILED

    def test_options_failure_fails_the_step_without_prompting(self, ask_run: AskRun) -> None:
        triage = _triage_step("nothing")
        prompter = _ScriptedPrompter([])

        outcome = ask_run.start([triage], prompter)

        assert outcome.status == SessionStatus.FAILED
        assert "(ASK_OPTIONS_ARTIFACT_NOT_FOUND)" in outcome.errors[0]
        assert prompter.requests == []

    def test_empty_multiselect_completes_without_prompting(self, ask_run: AskRun) -> None:
        producer = _command(
            "review", "echo '{\"findings\": []}' > review.json", artifacts=[{"name": "review", "path": "review.json"}]
        )
        triage = _triage_step("review")
        prompter = _ScriptedPrompter([])

        outcome = ask_run.start([producer, triage], prompter)

        assert outcome.status == SessionStatus.COMPLETED
        assert "No options to choose from for 'triage'; continuing with an empty selection." in outcome.warnings
        assert prompter.requests == []
        assert ask_run.leaf("triage").state == NodeState.COMPLETED

    def test_loop_iteration_two_sees_iteration_one_comment(self, ask_run: AskRun) -> None:
        loop: dict[str, object] = {
            "id": "plan-loop",
            "type": "loop",
            "max_iterations": 3,
            "until": ['steps.approve.outputs.selected == "approve"'],
            "do": [
                _command("plan", 'echo "[{{ steps.approve.outputs.comment }}]" >> seen.txt'),
                _approve_step(),
            ],
        }
        prompter = _ScriptedPrompter([_answer("revise", comment="Keep config compatible"), _answer("approve")])

        outcome = ask_run.start([loop], prompter)

        assert outcome.status == SessionStatus.COMPLETED
        assert ask_run.file("seen.txt").read_text(encoding="utf-8").splitlines() == ["[]", "[Keep config compatible]"]
        loop_node = ask_run.state().nodes[0]
        assert isinstance(loop_node, ExecutionLoopNode)
        assert [iteration.until_passed for iteration in loop_node.iterations] == [False, True]

    def test_paused_leaf_without_pending_ask_still_follows_the_failure_gate(self, ask_run: AskRun) -> None:
        steps = [_approve_step(on_failure="prompt_user")]
        seed_new_session(ask_run.paths, ask_run.sessions, session_id=_SESSION_ID, steps=steps)
        ask_run.execute(_ScriptedPrompter([_answer("deny")]), ScriptedFailurePrompter([KeyboardInterrupt()]))
        assert (ask_run.leaf("approve").state, ask_run.leaf("approve").pending_ask) == (NodeState.PAUSED, None)
        failure_prompter = ScriptedFailurePrompter([FailurePromptDecision.ABORT])
        interaction_prompter = _ScriptedPrompter([])

        outcome = ask_run.execute(interaction_prompter, failure_prompter)

        assert [result.step_id for result in failure_prompter.prompted] == ["approve"]
        assert interaction_prompter.requests == []
        assert outcome.status == SessionStatus.FAILED
