"""Contract tests for RunCoordinator over `when` guards: skips, outcomes and resume."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.interaction import InteractionAnswer, InteractionRequest
from dovo.engine import EngineLoader, RunCoordinator, SessionStateStore
from dovo.engine.executors.models import StepResult
from dovo.engine.models import (
    FailurePromptDecision,
    InteractionPrompter,
    RunContext,
    RunOutcome,
    SkipCause,
    StepSkipNotice,
)
from dovo.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepSkip,
)
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.sessions import RecordingRunObserver, ScriptedFailurePrompter, seed_new_session

_SESSION_ID = "guarded"
_FAILED_CHECK = 'steps.check.status == "ignored"'


class _RecordingPrompter(InteractionPrompter):
    """InteractionPrompter that records every request and leaves it unanswered."""

    def __init__(self) -> None:
        self.requests: list[InteractionRequest] = []

    def ask(self, request: InteractionRequest) -> InteractionAnswer | None:
        self.requests.append(request)
        return None


def _coordinator(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    observer: RecordingRunObserver | None = None,
    prompter: ScriptedFailurePrompter | None = None,
    interaction_prompter: InteractionPrompter | None = None,
) -> RunCoordinator:
    log_dir = paths.logs_dir / _SESSION_ID
    log_dir.mkdir(parents=True, exist_ok=True)
    context = RunContext(
        session_id=_SESSION_ID,
        paths=paths,
        target_dir=paths.root_dir,
        session_tmp_dir=None,
        session_log_dir=log_dir,
        artifact_store=None,
        interaction_prompter=interaction_prompter,
        git=GitRunner(),
        clock=fixed_clock,
        new_id=fixed_new_id,
    )
    store = SessionStateStore(sessions, paths, _SESSION_ID)
    return RunCoordinator(store, context, observer=observer, prompter=prompter)


def _run(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    steps: list[dict[str, object]],
    *,
    observer: RecordingRunObserver | None = None,
    prompter: ScriptedFailurePrompter | None = None,
    interaction_prompter: InteractionPrompter | None = None,
) -> RunOutcome:
    seed_new_session(paths, sessions, session_id=_SESSION_ID, steps=steps)
    coordinator = _coordinator(
        paths, sessions, observer=observer, prompter=prompter, interaction_prompter=interaction_prompter
    )
    return coordinator.execute()


def _state(paths: WorkspacePaths, sessions: SessionsRepository) -> ExecutionStateTree:
    return SessionStateStore(sessions, paths, _SESSION_ID).load([])


def _node(state: ExecutionStateTree, node_id: str) -> ExecutionLeafNode | ExecutionLoopNode:
    node = next(node for node in state.nodes if node.id == node_id)
    assert isinstance(node, ExecutionLeafNode | ExecutionLoopNode)
    return node


def _skip(node: ExecutionLeafNode | ExecutionLoopNode) -> tuple[NodeState, StepSkip | None]:
    """Return a node's state and skip record."""
    return node.state, node.skip


def _step_events(paths: WorkspacePaths, step_id: str) -> list[str]:
    log = (paths.logs_dir / _SESSION_ID / "session.log").read_text(encoding="utf-8")
    return [line for line in log.splitlines() if f'"step_id": "{step_id}"' in line]


def _mark_check_ignored(paths: WorkspacePaths, sessions: SessionsRepository) -> None:
    """Rewrite the saved result of top-level step `check` as ignored, as if it had failed and continued."""
    store = SessionStateStore(sessions, paths, _SESSION_ID)
    state = store.load([])
    check = _node(state, "check")
    assert isinstance(check, ExecutionLeafNode)
    ignored = StepResult(step_id="check", status="ignored", exit_code=0, stdout="", stderr="", duration_seconds=0.0)
    check.attempts[-1] = check.attempts[-1].model_copy(update={"result": ignored})
    store.save(state, [])


class WhenGuardTests:
    def test_failing_guard_skips_the_step_without_an_attempt_and_the_run_completes(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "fix", "name": "Fix it", "run": "touch fix.marker", "when": [_FAILED_CHECK]},
            {"id": "done", "run": "true"},
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        fix = _node(_state(engine_paths, sessions_repo), "fix")
        assert outcome.status == SessionStatus.COMPLETED
        assert [result.step_id for result in outcome.step_results] == ["check", "done"]
        assert _skip(fix) == (NodeState.SKIPPED, StepSkip(cause=SkipCause.WHEN_FALSE, expression=_FAILED_CHECK))
        assert isinstance(fix, ExecutionLeafNode) and fix.attempts == []
        assert _step_events(engine_paths, "fix") == []
        assert not (engine_workspace / "fix.marker").exists()

    def test_skip_notifies_the_observer_once_with_its_position(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        observer = RecordingRunObserver()
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "fix", "name": "Fix it", "run": "true", "when": [_FAILED_CHECK]},
            {
                "id": "loop",
                "type": "loop",
                "max_iterations": 1,
                "until": ["iteration.index >= 1"],
                "do": [{"id": "probe", "run": "true"}, {"id": "redo", "run": "true", "when": [_FAILED_CHECK]}],
            },
        ]

        _run(engine_paths, sessions_repo, steps, observer=observer)

        assert [call for call in observer.calls if call[0] == "step_skipped"] == [
            ("step_skipped", 2, 3, StepSkipNotice("fix", "Fix it", SkipCause.WHEN_FALSE, _FAILED_CHECK)),
            ("step_skipped", 2, 2, StepSkipNotice("redo", None, SkipCause.WHEN_FALSE, _FAILED_CHECK)),
        ]

    def test_passing_guard_runs_the_step(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "false", "on_failure": "continue"},
            {"id": "fix", "run": "touch fix.marker", "when": [_FAILED_CHECK]},
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        assert outcome.status == SessionStatus.COMPLETED
        assert _node(_state(engine_paths, sessions_repo), "fix").state is NodeState.COMPLETED
        assert (engine_workspace / "fix.marker").exists()

    def test_first_failing_expression_names_the_cause_and_an_unresolved_operand_is_false(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {
                "id": "fix",
                "run": "true",
                "when": ["steps.check.outputs.verdict == 'bad'", "steps.check.exit_code == 1"],
            },
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        fix = _node(_state(engine_paths, sessions_repo), "fix")
        assert outcome.status == SessionStatus.COMPLETED
        assert _skip(fix) == (
            NodeState.SKIPPED,
            StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.check.outputs.verdict == 'bad'"),
        )

    def test_later_condition_and_placeholder_on_a_skipped_step_resolve_as_never_ran(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "fix", "run": "echo '{\"n\": 1}'", "when": [_FAILED_CHECK]},
            {"id": "report", "run": "true", "when": ["steps.fix.exit_code == 0"]},
            {"id": "echo", "run": "echo '[${{ steps.fix.outputs.n }}]' > echo.txt"},
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        state = _state(engine_paths, sessions_repo)
        assert outcome.status == SessionStatus.COMPLETED
        assert _skip(_node(state, "report")) == (
            NodeState.SKIPPED,
            StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.fix.exit_code == 0"),
        )
        assert (engine_workspace / "echo.txt").read_text(encoding="utf-8") == "[]\n"

    def test_skipped_ask_neither_prompts_nor_pauses(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        prompter = _RecordingPrompter()
        ask = {"mode": "confirm", "message": "Fix it?"}
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "confirm", "type": "ask", "interaction": ask, "when": [_FAILED_CHECK]},
            {"id": "done", "run": "true"},
        ]

        outcome = _run(engine_paths, sessions_repo, steps, interaction_prompter=prompter)

        assert (outcome.status, outcome.paused_prompt_id) == (SessionStatus.COMPLETED, None)
        assert prompter.requests == []
        assert _node(_state(engine_paths, sessions_repo), "confirm").state is NodeState.SKIPPED

    def test_skipped_loop_runs_no_iteration(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        observer = RecordingRunObserver()
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {
                "id": "repair",
                "type": "loop",
                "when": [_FAILED_CHECK],
                "until": ["steps.fix.exit_code == 0"],
                "do": [{"id": "fix", "run": "touch fix.marker"}],
            },
        ]

        outcome = _run(engine_paths, sessions_repo, steps, observer=observer)

        repair = _node(_state(engine_paths, sessions_repo), "repair")
        assert outcome.status == SessionStatus.COMPLETED
        assert _skip(repair) == (NodeState.SKIPPED, StepSkip(cause=SkipCause.WHEN_FALSE, expression=_FAILED_CHECK))
        assert [call[0] for call in observer.calls if str(call[0]).startswith("loop_")] == []
        assert not (engine_workspace / "fix.marker").exists()

    def test_body_guard_is_evaluated_on_every_iteration(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        steps: list[dict[str, object]] = [
            {
                "id": "loop",
                "type": "loop",
                "max_iterations": 3,
                "until": ["iteration.index >= 3"],
                "do": [
                    {"id": "probe", "run": "test -f flag", "on_failure": "continue"},
                    {"id": "fix", "run": "true", "when": ['steps.probe.status == "completed"']},
                    {"id": "mark", "run": "touch flag"},
                ],
            }
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        loop = _node(_state(engine_paths, sessions_repo), "loop")
        assert isinstance(loop, ExecutionLoopNode)
        assert outcome.status == SessionStatus.COMPLETED
        assert [iteration.steps[1].state for iteration in loop.iterations] == [
            NodeState.SKIPPED,
            NodeState.COMPLETED,
            NodeState.COMPLETED,
        ]

    def test_every_step_skipped_completes_the_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        steps: list[dict[str, object]] = [
            {
                "id": "loop",
                "type": "loop",
                "max_iterations": 1,
                "until": ["iteration.index >= 1"],
                "do": [
                    {"id": "a", "run": "true", "when": ["iteration.index > 1"]},
                    {"id": "b", "run": "true", "when": ["steps.a.exit_code == 0"]},
                ],
            },
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        loop = _node(_state(engine_paths, sessions_repo), "loop")
        assert isinstance(loop, ExecutionLoopNode)
        assert outcome.status == SessionStatus.COMPLETED
        assert [leaf.state for leaf in loop.iterations[0].steps] == [NodeState.SKIPPED, NodeState.SKIPPED]
        assert outcome.step_results == []

    def test_step_skipped_before_a_pause_stays_skipped_on_resume(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "fix", "run": "touch fix.marker", "when": [_FAILED_CHECK]},
            {"id": "gate", "run": "test -f allow", "on_failure": "prompt_user"},
        ]
        paused = _run(engine_paths, sessions_repo, steps, prompter=ScriptedFailurePrompter([KeyboardInterrupt()]))
        _mark_check_ignored(engine_paths, sessions_repo)
        (engine_workspace / "allow").touch()

        observer = RecordingRunObserver()
        resumed = _coordinator(
            engine_paths,
            sessions_repo,
            observer=observer,
            prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY]),
        ).execute()

        assert (paused.status, resumed.status) == (SessionStatus.PAUSED, SessionStatus.COMPLETED)
        assert _node(_state(engine_paths, sessions_repo), "fix").state is NodeState.SKIPPED
        assert [call for call in observer.calls if call[0] == "step_skipped"] == []
        assert not (engine_workspace / "fix.marker").exists()

    def test_resume_of_a_run_whose_every_node_is_skipped_completes(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        steps: list[dict[str, object]] = [{"id": "a", "run": "true"}, {"id": "b", "run": "true"}]
        seed_new_session(engine_paths, sessions_repo, session_id=_SESSION_ID, steps=steps)
        store = SessionStateStore(sessions_repo, engine_paths, _SESSION_ID)
        state = store.load([])
        skip = StepSkip(cause=SkipCause.WHEN_FALSE, expression="steps.x.exit_code == 0")
        state.nodes[0] = ExecutionLeafNode(id="a", state=NodeState.SKIPPED, skip=skip)
        state.nodes[1] = ExecutionLeafNode(id="b", state=NodeState.SKIPPED, skip=skip)
        store.save(state, [])
        sessions_repo.update_status(_SESSION_ID, SessionStatus.PAUSED)
        EngineLoader.load_for_resume(sessions_repo, engine_paths, _SESSION_ID, [])

        outcome = _coordinator(engine_paths, sessions_repo).execute()

        assert (outcome.status, outcome.step_results) == (SessionStatus.COMPLETED, [])
