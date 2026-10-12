"""Shared test harness for seeding persisted session rows and execution state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine import RunCoordinator, RunObserver, RunOutcome
from dovo.engine.executors.models import ConditionEvaluationResult, StepResult
from dovo.engine.models import RunContext
from dovo.engine.state_models import ExecutionLeafNode, NodeState, StepAttemptRecord
from dovo.engine.state_store import SessionStateStore
from dovo.engine.writer import snapshot_definitions
from tests.harness.catalog import write_runnable_blueprint

SEEDED_FAILURE = "seeded failure"


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

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
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

    def on_worktree_cleanup(self, kept: bool, path: Path) -> None:
        pass

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        pass

    def on_run_completed(self, outcome: RunOutcome) -> None:
        pass


def seed_new_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    use_worktree: bool = False,
    keep: bool = False,
    auto_apply: bool = False,
    agent: str | None = None,
) -> SessionRecord:
    """Write and snapshot a catalog blueprint, insert a RUNNING row, and initialize its all-pending state tree."""
    write_runnable_blueprint(paths.root_dir, key=session_id, steps=steps)
    catalog = Catalog(paths)
    blueprint = Blueprint.load(session_id, catalog=catalog)
    manifest = snapshot_definitions(catalog, blueprint, paths.session_dir(session_id), [])
    assert manifest is not None

    sessions.create(
        session_id,
        blueprint_name=session_id,
        blueprint_key=session_id,
        status=SessionStatus.RUNNING,
        use_worktree=use_worktree,
        keep=keep,
        inputs_json="{}",
        auto_apply=auto_apply,
        agent=agent,
    )
    assert SessionStateStore(sessions, paths, session_id).initialize(blueprint, manifest).ok
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
    """Write and snapshot a catalog blueprint, initialize its state, complete steps before paused_step_id, and PAUSE that leaf on a failed attempt."""
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
    state = store.load().state
    assert state is not None

    now = datetime.now(UTC).isoformat()
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
        node.attempts = [StepAttemptRecord(number=1, started_at=now, completed_at=now, result=result)]
        node.state = NodeState.COMPLETED if completed else NodeState.PAUSED
        if not completed:
            break

    saved = store.save(
        state,
        run_status=SessionStatus.PAUSED,
        error_message=f"Step '{paused_step_id}' failed: {SEEDED_FAILURE}",
        worktree_id=worktree_id,
    )
    assert saved.ok
    row = sessions.get(session_id)
    assert row is not None
    return row


def seed_failed_session(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    failed_step_id: str,
) -> SessionRecord:
    """Seed a session whose steps before failed_step_id completed and whose failed_step_id attempt failed (exit 1, error_message SEEDED_FAILURE), saved with run_status=FAILED."""
    seed_new_session(paths, sessions, session_id=session_id, steps=steps)
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load().state
    assert state is not None

    now = datetime.now(UTC).isoformat()
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
            error_message=None if completed else SEEDED_FAILURE,
        )
        node.attempts = [StepAttemptRecord(number=1, started_at=now, completed_at=now, result=result)]
        node.state = NodeState.COMPLETED if completed else NodeState.FAILED
        if not completed:
            break

    saved = store.save(
        state,
        run_status=SessionStatus.FAILED,
        error_message=f"Step '{failed_step_id}' failed: {SEEDED_FAILURE}",
    )
    assert saved.ok
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
    """Seed a session and run it with no interaction prompter, leaving it PAUSED at its first ask step with a stored pending_ask."""
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
    )
    outcome = RunCoordinator(SessionStateStore(sessions, paths, session_id), context).execute()
    assert outcome.status is SessionStatus.PAUSED
    sessions.update_status(session_id, SessionStatus.PAUSED)
    row = sessions.get(session_id)
    assert row is not None
    return row
