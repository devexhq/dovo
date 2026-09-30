"""Shared test harness for seeding persisted run rows and execution state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from tests.harness.catalog import write_runnable_blueprint
from worktree.common.filesystem.models import WorkspacePaths
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.models import RunObserver, RunOutcome
from worktree.core.engine.state_models import ExecutionLeafNode, NodeState, StepAttemptRecord
from worktree.core.engine.state_store import RunStateStore
from worktree.core.engine.writer import snapshot_definitions
from worktree.core.step.models import ConditionEvaluationResult, LoopStepBlock, StepDefinition, StepResult

SEEDED_FAILURE = "seeded failure"


class NoOpRunObserver(RunObserver):
    """RunObserver ignoring every hook; subclass and override only the callbacks a test records."""

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

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        pass

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        pass

    def on_run_completed(self, outcome: RunOutcome) -> None:
        pass


def seed_new_run(
    paths: WorkspacePaths,
    runs: RunsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    use_sandbox: bool = False,
    keep: bool = False,
    auto_apply: bool = False,
) -> RunRecord:
    """Write and snapshot a catalog blueprint, insert a RUNNING row, and initialize its all-pending state tree."""
    write_runnable_blueprint(paths.root_dir, key=session_id, steps=steps)
    catalog = Catalog(paths)
    blueprint = Blueprint.load(session_id, catalog=catalog)
    manifest = snapshot_definitions(catalog, blueprint, paths.session_dir(session_id), [])
    assert manifest is not None

    runs.create(
        session_id,
        blueprint_name=session_id,
        blueprint_key=session_id,
        status=RunStatus.RUNNING,
        use_sandbox=use_sandbox,
        keep=keep,
        inputs_json="{}",
        auto_apply=auto_apply,
    )
    assert RunStateStore(runs, paths, session_id).initialize(blueprint, manifest).ok
    row = runs.get(session_id)
    assert row is not None
    return row


def seed_paused_run(
    paths: WorkspacePaths,
    runs: RunsRepository,
    *,
    session_id: str,
    steps: list[dict[str, object]],
    paused_step_id: str,
    use_sandbox: bool = False,
    sandbox_id: str | None = None,
    auto_apply: bool = False,
) -> RunRecord:
    """Write and snapshot a catalog blueprint, initialize its state, complete steps before paused_step_id, and PAUSE that leaf on a failed attempt."""
    seed_new_run(paths, runs, session_id=session_id, steps=steps, use_sandbox=use_sandbox, auto_apply=auto_apply)
    store = RunStateStore(runs, paths, session_id)
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
        run_status=RunStatus.PAUSED,
        error_message=f"Step '{paused_step_id}' failed: {SEEDED_FAILURE}",
        sandbox_id=sandbox_id,
    )
    assert saved.ok
    row = runs.get(session_id)
    assert row is not None
    return row
