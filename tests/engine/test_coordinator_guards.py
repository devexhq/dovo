"""Contract tests for RunCoordinator over `when` guards and `if` blocks: skips, branches, outcomes and resume."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.engine import RunCoordinator, SessionStateStore
from dovo.engine.executors.models import ConditionEvaluationResult, StepResult
from dovo.engine.models import (
    FailurePromptDecision,
    IfBranch,
    IfBranchNotice,
    RunContext,
    RunOutcome,
    SkipCause,
    StepSkipNotice,
)
from dovo.engine.state_models import (
    ExecutionIfNode,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    StepSkip,
)
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.sessions import RecordingRunObserver, ScriptedFailurePrompter, seed_new_session

_FAILED_CHECK = 'steps.check.status == "ignored"'


def _coordinator(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    session_id: str,
    *,
    observer: RecordingRunObserver | None = None,
    prompter: ScriptedFailurePrompter | None = None,
) -> RunCoordinator:
    log_dir = paths.logs_dir / session_id
    log_dir.mkdir(parents=True, exist_ok=True)
    context = RunContext(
        session_id=session_id,
        paths=paths,
        target_dir=paths.root_dir,
        session_tmp_dir=None,
        session_log_dir=log_dir,
        artifact_store=None,
        git=GitRunner(),
        clock=fixed_clock,
        new_id=fixed_new_id,
    )
    return RunCoordinator(SessionStateStore(sessions, paths, session_id), context, observer=observer, prompter=prompter)


def _run(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    steps: list[dict[str, object]],
    *,
    observer: RecordingRunObserver | None = None,
    prompter: ScriptedFailurePrompter | None = None,
) -> RunOutcome:
    seed_new_session(paths, sessions, session_id="guarded", steps=steps)
    return _coordinator(paths, sessions, "guarded", observer=observer, prompter=prompter).execute()


def _state(paths: WorkspacePaths, sessions: SessionsRepository) -> ExecutionStateTree:
    return SessionStateStore(sessions, paths, "guarded").load([])


def _node(state: ExecutionStateTree, node_id: str) -> ExecutionLeafNode | ExecutionLoopNode | ExecutionIfNode:
    return next(node for node in state.nodes if node.id == node_id)


def _skip(node: object) -> tuple[object, object]:
    """Return a node's state and skip record."""
    assert isinstance(node, ExecutionLeafNode | ExecutionLoopNode)
    return node.state, node.skip


def _step_events(paths: WorkspacePaths, step_id: str) -> list[str]:
    log = (paths.logs_dir / "guarded" / "session.log").read_text(encoding="utf-8")
    return [line for line in log.splitlines() if f'"step_id": "{step_id}"' in line]


class WhenGuardTests:
    def test_failing_guard_skips_the_step_without_an_attempt_and_the_run_completes(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        observer = RecordingRunObserver()
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "fix", "name": "Fix it", "run": "touch fix.marker", "when": [_FAILED_CHECK]},
            {"id": "done", "run": "true"},
        ]

        outcome = _run(engine_paths, sessions_repo, steps, observer=observer)

        fix = _node(_state(engine_paths, sessions_repo), "fix")
        assert outcome.status == SessionStatus.COMPLETED
        assert [result.step_id for result in outcome.step_results] == ["check", "done"]
        assert _skip(fix) == (NodeState.SKIPPED, StepSkip(cause=SkipCause.WHEN_FALSE, expression=_FAILED_CHECK))
        assert isinstance(fix, ExecutionLeafNode) and fix.attempts == []
        assert ("step_skipped", 2, 3, StepSkipNotice("fix", "Fix it", SkipCause.WHEN_FALSE, _FAILED_CHECK)) in (
            observer.calls
        )
        assert _step_events(engine_paths, "fix") == []
        assert not (engine_workspace / "fix.marker").exists()

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

    def test_step_skipped_before_a_pause_stays_skipped_on_resume(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            {"id": "fix", "run": "touch fix.marker", "when": [_FAILED_CHECK]},
            {"id": "gate", "run": "test -f allow", "on_failure": "prompt_user"},
        ]
        paused = _run(engine_paths, sessions_repo, steps, prompter=ScriptedFailurePrompter([KeyboardInterrupt()]))
        store = SessionStateStore(sessions_repo, engine_paths, "guarded")
        state = store.load([])
        check = _node(state, "check")
        assert isinstance(check, ExecutionLeafNode)
        failed = StepResult(step_id="check", status="ignored", exit_code=0, stdout="", stderr="", duration_seconds=0.0)
        check.attempts[-1] = check.attempts[-1].model_copy(update={"result": failed})
        store.save(state, [])
        (engine_workspace / "allow").touch()

        resumed = _coordinator(
            engine_paths, sessions_repo, "guarded", prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        ).execute()

        assert (paused.status, resumed.status) == (SessionStatus.PAUSED, SessionStatus.COMPLETED)
        assert _node(_state(engine_paths, sessions_repo), "fix").state is NodeState.SKIPPED
        assert not (engine_workspace / "fix.marker").exists()


def _route(**fields: object) -> dict[str, object]:
    block: dict[str, object] = {
        "id": "route",
        "type": "if",
        "condition": [_FAILED_CHECK],
        "then": [{"id": "fix", "run": "touch fix.marker"}],
        "else": [{"id": "ship", "run": "touch ship.marker"}],
    }
    return {**block, **fields}


def _if_node(state: ExecutionStateTree) -> ExecutionIfNode:
    node = _node(state, "route")
    assert isinstance(node, ExecutionIfNode)
    return node


class IfBlockTests:
    def test_true_condition_runs_then_and_not_else(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        observer = RecordingRunObserver()
        steps: list[dict[str, object]] = [{"id": "check", "run": "false", "on_failure": "continue"}, _route()]

        outcome = _run(engine_paths, sessions_repo, steps, observer=observer)

        route = _if_node(_state(engine_paths, sessions_repo))
        assert outcome.status == SessionStatus.COMPLETED
        assert (route.state, route.branch, [leaf.id for leaf in route.steps]) == (
            NodeState.COMPLETED,
            IfBranch.THEN,
            ["fix"],
        )
        branch_calls = [call for call in observer.calls if call[0] == "if_branch"]
        assert branch_calls == [
            (
                "if_branch",
                2,
                2,
                IfBranchNotice(
                    "route",
                    None,
                    IfBranch.THEN,
                    (
                        ConditionEvaluationResult(
                            expression=_FAILED_CHECK, passed=True, actual="ignored", expected="ignored", detail="TRUE"
                        ),
                    ),
                ),
            )
        ]
        assert (engine_workspace / "fix.marker").exists()
        assert not (engine_workspace / "ship.marker").exists()

    def test_false_condition_without_else_runs_nothing_and_completes(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        route = {key: value for key, value in _route().items() if key != "else"}
        steps: list[dict[str, object]] = [{"id": "check", "run": "true"}, route, {"id": "done", "run": "true"}]

        outcome = _run(engine_paths, sessions_repo, steps)

        route = _if_node(_state(engine_paths, sessions_repo))
        assert outcome.status == SessionStatus.COMPLETED
        assert (route.state, route.branch, route.steps) == (NodeState.COMPLETED, IfBranch.NONE, [])
        assert not (engine_workspace / "fix.marker").exists()

    def test_failing_branch_step_fails_the_block_and_the_run(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            _route(**{"else": [{"id": "ship", "run": "false"}, {"id": "after", "run": "touch after.marker"}]}),
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        route = _if_node(_state(engine_paths, sessions_repo))
        assert outcome.status == SessionStatus.FAILED
        assert (route.state, [leaf.state for leaf in route.steps]) == (
            NodeState.FAILED,
            [NodeState.FAILED, NodeState.PENDING],
        )
        assert not (engine_workspace / "after.marker").exists()

    def test_ignored_branch_step_leaves_the_block_completed(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            _route(**{"else": [{"id": "ship", "run": "false", "on_failure": "continue"}]}),
        ]

        outcome = _run(engine_paths, sessions_repo, steps)

        assert outcome.status == SessionStatus.COMPLETED
        assert _if_node(_state(engine_paths, sessions_repo)).state is NodeState.COMPLETED

    def test_run_paused_in_a_branch_resumes_in_it_without_choosing_again(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, engine_workspace: Path
    ) -> None:
        steps: list[dict[str, object]] = [
            {"id": "check", "run": "true"},
            _route(
                **{
                    "else": [
                        {"id": "gate", "run": "test -f allow", "on_failure": "prompt_user"},
                        {"id": "ship", "run": "touch ship.marker"},
                    ]
                }
            ),
            {"id": "after", "run": "true", "when": ["steps.fix.exit_code == 0"]},
        ]
        paused = _run(engine_paths, sessions_repo, steps, prompter=ScriptedFailurePrompter([KeyboardInterrupt()]))
        store = SessionStateStore(sessions_repo, engine_paths, "guarded")
        state = store.load([])
        route_paused = _if_node(state)
        check = _node(state, "check")
        assert isinstance(check, ExecutionLeafNode)
        failed = StepResult(step_id="check", status="ignored", exit_code=0, stdout="", stderr="", duration_seconds=0.0)
        check.attempts[-1] = check.attempts[-1].model_copy(update={"result": failed})
        store.save(state, [])
        (engine_workspace / "allow").touch()

        resumed = _coordinator(
            engine_paths, sessions_repo, "guarded", prompter=ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        ).execute()

        final = _state(engine_paths, sessions_repo)
        assert (paused.status, route_paused.state, route_paused.branch) == (
            SessionStatus.PAUSED,
            NodeState.PAUSED,
            IfBranch.ELSE,
        )
        assert resumed.status == SessionStatus.COMPLETED
        assert (_if_node(final).state, _if_node(final).branch) == (NodeState.COMPLETED, IfBranch.ELSE)
        assert _skip(_node(final, "after")) == (
            NodeState.SKIPPED,
            StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.fix.exit_code == 0"),
        )
        assert (engine_workspace / "ship.marker").exists()
        assert not (engine_workspace / "fix.marker").exists()
