"""Shared test harness for seeding persisted session rows and execution state."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import count
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.models import Remediation
from dovo.common.timing import TimeSpan
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import BlueprintStep, LoopStepBlock, StepDefinition
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus, WorktreesRepository
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.git.runner import GitRunner
from dovo.engine import RunCoordinator, RunObserver, RunOutcome
from dovo.engine.executors.models import AgentProgressUpdate, ConditionEvaluationResult, StepResult
from dovo.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    IfBranchNotice,
    LifecycleUpdate,
    LoopPromptDecision,
    RunContext,
    StepSkipNotice,
)
from dovo.engine.state_models import ExecutionLeafNode, NodeState, StepAttemptRecord
from dovo.engine.state_store import SessionStateStore
from dovo.engine.writer import snapshot_definitions
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.fakes import fixed_clock, fixed_new_id

SEEDED_FAILURE = "seeded failure"
SEEDED_SPAN = TimeSpan(
    started_at="2026-09-28T00:00:00+00:00", ended_at="2026-09-28T00:00:01+00:00", duration_seconds=1.0
)


def fail_reconciliation_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch SessionsRepository.list so its first call, made by reconciliation, raises RuntimeError("db unavailable") and every later call lists normally."""
    original_list = SessionsRepository.list
    calls = count()

    def _list(
        self: SessionsRepository, limit: int | None = None, status: SessionStatus | str | None = None
    ) -> list[SessionRecord]:
        if next(calls) == 0:
            raise RuntimeError("db unavailable")
        return original_list(self, limit=limit, status=status)

    monkeypatch.setattr(SessionsRepository, "list", _list)


class NoOpRunObserver(RunObserver):
    """RunObserver ignoring every hook; subclass and override only the callbacks a test records."""

    def on_worktree_ready(self, path: Path, active: bool) -> None:
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

    def on_step_progress(self, idx: int, total: int, step: StepDefinition, update: AgentProgressUpdate) -> None:
        pass

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        pass

    def on_step_skipped(self, idx: int, total: int, step: StepSkipNotice) -> None:
        pass

    def on_if_branch(self, idx: int, total: int, block: IfBranchNotice) -> None:
        pass

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        pass

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        pass

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        pass

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        pass

    def on_worktree_cleanup(
        self,
        kept: bool,
        path: Path,
        worktree_id: str | None = None,
        branch: str | None = None,
        remediations: Sequence[Remediation] = (),
    ) -> None:
        pass

    def on_run_started(self, steps: Sequence[BlueprintStep]) -> None:
        pass

    def on_run_completed(self, outcome: RunOutcome) -> None:
        pass


def _recorded(value: object) -> object:
    """Reduce a hook argument to what observer tests compare: ids for steps and loops, statuses, passed flags."""
    if isinstance(value, StepDefinition | LoopStepBlock):
        return value.id
    if isinstance(value, StepResult):
        return value.status
    if isinstance(value, ConditionEvaluationResult):
        return value.passed
    if isinstance(value, list | tuple):
        return [_recorded(item) for item in value]
    return value


class RecordingRunObserver(NoOpRunObserver):
    """Record every observer hook call, in order, as (hook name, arguments).

    Each call is one flat tuple: the hook name without its ``on_`` prefix, then every argument
    in signature order. Steps and loops are recorded by id, a StepResult by its status and each
    condition result by whether it passed; paths, outcomes and progress updates are kept as passed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def _record(self, hook: str, *args: object) -> None:
        self.calls.append((hook, *(_recorded(arg) for arg in args)))

    def on_worktree_ready(self, path: Path, active: bool) -> None:
        self._record("worktree_ready", path, active)

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        self._record("step_start", idx, total, step)

    def on_step_output(
        self,
        idx: int,
        total: int,
        step: StepDefinition,
        line: str,
        stream: str = "stdout",
    ) -> None:
        self._record("step_output", idx, total, step, line, stream)

    def on_step_progress(self, idx: int, total: int, step: StepDefinition, update: AgentProgressUpdate) -> None:
        self._record("step_progress", idx, total, step, update)

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        self._record("step_done", idx, total, step, result)

    def on_step_skipped(self, idx: int, total: int, step: StepSkipNotice) -> None:
        self._record("step_skipped", idx, total, step)

    def on_if_branch(self, idx: int, total: int, block: IfBranchNotice) -> None:
        self._record("if_branch", idx, total, block)

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        self._record("loop_start", loop_id, max_iterations)

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        self._record("loop_iteration_start", loop_id, iteration, max_iterations)

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        self._record("loop_conditions_evaluated", loop_id, results, all_passed, next_iteration)

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        self._record("loop_done", loop_id, status, total_iterations)

    def on_worktree_cleanup(
        self,
        kept: bool,
        path: Path,
        worktree_id: str | None = None,
        branch: str | None = None,
        remediations: Sequence[Remediation] = (),
    ) -> None:
        self._record("worktree_cleanup", kept, path, worktree_id, branch, list(remediations))

    def on_run_started(self, steps: Sequence[BlueprintStep]) -> None:
        self._record("run_started", steps)

    def on_run_completed(self, outcome: RunOutcome) -> None:
        self._record("run_completed", outcome)


type ScriptedDecision = FailurePromptDecision | LoopPromptDecision | BaseException


class ScriptedFailurePrompter(FailurePrompter):
    """Return queued decisions in order and record each prompted step; raise KeyboardInterrupt when queued.

    A queued exception instance is raised instead of answering. ``prompted`` holds each failed
    StepResult and ``diagnostics`` the diagnostic shown with it; ``loop_prompts`` holds the
    (iteration, grant_count) of each max-iterations prompt. ``on_prompt`` runs before a
    step-failure answer is taken from the queue.
    """

    def __init__(
        self, decisions: Sequence[ScriptedDecision] = (), *, on_prompt: Callable[[], None] | None = None
    ) -> None:
        self.decisions: list[ScriptedDecision] = list(decisions)
        self.on_prompt = on_prompt
        self.prompted: list[StepResult] = []
        self.diagnostics: list[str] = []
        self.loop_prompts: list[tuple[int, int]] = []

    def _next(self) -> FailurePromptDecision | LoopPromptDecision:
        assert self.decisions, "ScriptedFailurePrompter was prompted with no decision queued"
        decision = self.decisions.pop(0)
        if isinstance(decision, BaseException):
            raise decision
        return decision

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        self.prompted.append(result)
        self.diagnostics.append(diagnostic)
        if self.on_prompt is not None:
            self.on_prompt()
        decision = self._next()
        assert isinstance(decision, FailurePromptDecision)
        return decision

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        self.loop_prompts.append((iteration, grant_count))
        decision = self._next()
        assert isinstance(decision, LoopPromptDecision)
        return decision


def seed_new_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    use_worktree: bool = False,
    keep: bool = False,
    discard: bool = False,
    auto_apply: bool = False,
    agent: str | None = None,
    inputs_json: str | None = "{}",
) -> SessionRecord:
    """Write and snapshot a catalog blueprint, insert a RUNNING row, and initialize its all-pending state tree."""
    write_runnable_blueprint(paths.root_dir, key=session_id, steps=steps)
    catalog = Catalog(paths)
    blueprint = Blueprint.load(session_id, catalog=catalog)
    manifest = snapshot_definitions(catalog, blueprint, paths.session_dir(session_id), [], [])
    assert manifest is not None

    sessions.create(
        session_id,
        blueprint_name=session_id,
        blueprint_key=session_id,
        status=SessionStatus.RUNNING,
        use_worktree=use_worktree,
        keep=keep,
        discard=discard,
        inputs_json=inputs_json,
        auto_apply=auto_apply,
        agent=agent,
    )
    SessionStateStore(sessions, paths, session_id).initialize(blueprint, manifest, [])
    row = sessions.get(session_id)
    assert row is not None
    return row


def seed_crashed_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    interrupted_step_id: str,
    use_worktree: bool = False,
    worktree_id: str | None = None,
    worktree_path: Path | None = None,
    auto_apply: bool = False,
) -> SessionRecord:
    """Seed a RUNNING row with no pid whose interrupted_step_id leaf is RUNNING on one open attempt, as a crash leaves it.

    worktree_id is recorded on the row; with worktree_path too, a worktree row with that id and path is created.
    """
    seed_new_session(
        paths, sessions, session_id=session_id, steps=steps, use_worktree=use_worktree, auto_apply=auto_apply
    )
    if worktree_id is not None and worktree_path is not None:
        WorktreesRepository(db_path=sessions.db_path, project_id=sessions.project_id).create(
            id=worktree_id, branch_name=f"dovo/{worktree_id}", base_commit="0" * 40, worktree_path=worktree_path
        )
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load([])

    for node in state.nodes:
        if isinstance(node, ExecutionLeafNode) and node.id == interrupted_step_id:
            node.state = NodeState.RUNNING
            node.attempts = [StepAttemptRecord(number=1, span=TimeSpan(started_at=SEEDED_SPAN.started_at))]

    store.save(state, [], LifecycleUpdate(worktree_id=worktree_id))
    row = sessions.get(session_id)
    assert row is not None
    return row


def seed_paused_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    paused_step_id: str,
    use_worktree: bool = False,
    worktree_id: str | None = None,
    auto_apply: bool = False,
    agent: str | None = None,
) -> SessionRecord:
    """Seed a session whose earlier steps completed and whose paused_step_id leaf is PAUSED on a failed attempt."""
    seed_new_session(
        paths,
        sessions,
        session_id=session_id,
        steps=steps,
        use_worktree=use_worktree,
        auto_apply=auto_apply,
        agent=agent,
    )
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load([])

    for node in state.nodes:
        assert isinstance(node, ExecutionLeafNode)
        completed = node.id != paused_step_id
        result = StepResult(
            step_id=node.id,
            status="completed" if completed else "failed",
            exit_code=0 if completed else 1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=None if completed else SEEDED_FAILURE,
        )
        node.attempts = [StepAttemptRecord(number=1, span=SEEDED_SPAN, result=result)]
        node.state = NodeState.COMPLETED if completed else NodeState.PAUSED
        if not completed:
            break

    store.save(
        state,
        [],
        LifecycleUpdate(
            run_status=SessionStatus.PAUSED,
            error_message=f"Step '{paused_step_id}' failed: {SEEDED_FAILURE}",
            worktree_id=worktree_id,
        ),
    )
    row = sessions.get(session_id)
    assert row is not None
    return row


def _seeded_error_message(rejected_patch_path: str | None) -> str:
    """Return SEEDED_FAILURE, followed by the production 'Discarded edit saved to' line when a patch path is given."""
    if rejected_patch_path is None:
        return SEEDED_FAILURE

    return f"{SEEDED_FAILURE}\nDiscarded edit saved to {rejected_patch_path}."


def seed_failed_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    failed_step_id: str,
    rejected_patch_path: str | None = None,
) -> SessionRecord:
    """Seed a session whose earlier steps completed and whose failed_step_id attempt failed, saved FAILED.

    The failed attempt has exit 1 and error_message SEEDED_FAILURE,
    followed by a 'Discarded edit saved to' line when rejected_patch_path is given.
    """
    seed_new_session(paths, sessions, session_id=session_id, steps=steps)
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load([])

    for node in state.nodes:
        assert isinstance(node, ExecutionLeafNode)
        completed = node.id != failed_step_id
        result = StepResult(
            step_id=node.id,
            status="completed" if completed else "failed",
            exit_code=0 if completed else 1,
            stdout="",
            stderr="",
            duration_seconds=0.5,
            error_message=None if completed else _seeded_error_message(rejected_patch_path),
            rejected_patch_path=None if completed else rejected_patch_path,
        )
        node.attempts = [StepAttemptRecord(number=1, span=SEEDED_SPAN, result=result)]
        node.state = NodeState.COMPLETED if completed else NodeState.FAILED
        if not completed:
            break

    store.save(
        state,
        [],
        LifecycleUpdate(
            run_status=SessionStatus.FAILED,
            error_message=f"Step '{failed_step_id}' failed: {SEEDED_FAILURE}",
        ),
    )
    row = sessions.get(session_id)
    assert row is not None
    return row


def seed_paused_ask_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    artifacts: ArtifactsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
) -> SessionRecord:
    """Seed and run a session with no prompter, leaving it PAUSED at its first ask with a stored pending_ask."""
    seed_new_session(paths, sessions, session_id=session_id, steps=steps)
    session_tmp_dir = paths.tmp_dir / session_id
    (session_tmp_dir / "steps").mkdir(parents=True, exist_ok=True)
    session_log_dir = paths.logs_dir / session_id
    session_log_dir.mkdir(parents=True, exist_ok=True)
    context = RunContext(
        session_id=session_id,
        paths=paths,
        target_dir=paths.root_dir,
        session_tmp_dir=session_tmp_dir,
        session_log_dir=session_log_dir,
        artifact_store=ArtifactStore(Artifacts(paths, artifacts), session_id),
        git=GitRunner(),
        clock=fixed_clock,
        new_id=fixed_new_id,
    )
    outcome = RunCoordinator(SessionStateStore(sessions, paths, session_id), context).execute()
    assert outcome.status is SessionStatus.PAUSED
    sessions.update_status(session_id, SessionStatus.PAUSED)
    row = sessions.get(session_id)
    assert row is not None
    return row
