"""Contract tests for EngineLoader: paused-run validation before coordinator dispatch."""

from __future__ import annotations

import pytest

from tests.harness.runs import SEEDED_FAILURE, seed_new_run, seed_paused_run
from worktree.common.filesystem.models import WorkspacePaths
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine import EngineLoader, EngineResumeError, EngineResumeStatus, RunStateStore
from worktree.core.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    NodeState,
    StepAttemptRecord,
)
from worktree.core.engine.writer import snapshot_blueprint_path
from worktree.core.step.models import StepResult

_STEPS: list[dict[str, object]] = [{"id": "a", "run": "true"}, {"id": "b", "run": "exit 1"}]
_LOOP_STEPS: list[dict[str, object]] = [
    {
        "id": "fix",
        "type": "loop",
        "max_iterations": 2,
        "until": ["iteration.index >= 2"],
        "do": [{"id": "edit", "run": "true"}, {"id": "verify", "run": "exit 1"}],
    }
]


def _seed(
    paths: WorkspacePaths,
    runs: RunsRepository,
    *,
    use_sandbox: bool = False,
    sandbox_id: str | None = None,
) -> RunRecord:
    return seed_paused_run(
        paths,
        runs,
        session_id="paused-1",
        steps=_STEPS,
        paused_step_id="b",
        use_sandbox=use_sandbox,
        sandbox_id=sandbox_id,
    )


def _break_state(paths: WorkspacePaths, runs: RunsRepository, fault: str) -> None:
    """Corrupt the seeded paused run's state document in the way fault names."""
    store = RunStateStore(runs, paths, "paused-1")
    loaded = store.load().state
    assert loaded is not None
    if fault == "paused-leaf-without-result":
        paused = loaded.nodes[1]
        assert isinstance(paused, ExecutionLeafNode)
        paused.attempts[-1] = paused.attempts[-1].model_copy(update={"result": None})
    else:
        for node in loaded.nodes:
            node.state = NodeState.COMPLETED
    assert store.save(loaded).ok


def _seed_paused_loop(paths: WorkspacePaths, runs: RunsRepository) -> RunStateStore:
    """Seed a paused run whose loop `fix` has completed body step `edit` and PAUSED body step `verify` on a failed attempt."""
    seed_new_run(paths, runs, session_id="paused-1", steps=_LOOP_STEPS)
    row = runs.get("paused-1")
    assert row is not None
    runs.update_status("paused-1", RunStatus.PAUSED)
    store = RunStateStore(runs, paths, "paused-1")
    state = store.load().state
    assert state is not None
    loop = state.nodes[0]
    assert isinstance(loop, ExecutionLoopNode)

    def attempt(step_id: str, status: str) -> StepAttemptRecord:
        result = StepResult(
            step_id=step_id,
            status=status,
            exit_code=0 if status == "completed" else 1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=None if status == "completed" else SEEDED_FAILURE,
        )
        return StepAttemptRecord(number=1, started_at="t", completed_at="t", result=result)

    loop.state = NodeState.RUNNING
    loop.iterations[0].state = NodeState.RUNNING
    edit, verify = loop.iterations[0].steps
    edit.attempts = [attempt("edit", "completed")]
    edit.state = NodeState.COMPLETED
    verify.attempts = [attempt("verify", "failed")]
    verify.state = NodeState.PAUSED
    assert store.save(state, run_status=RunStatus.PAUSED).ok
    return store


class EngineLoaderTests:
    """[tier-1/integration] EngineLoader.load_for_resume: classify and validate a paused run before dispatch."""

    def test_paused_run_returns_row_state_and_manifest(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused row with a PAUSED leaf and intact snapshots returns (row, state, state.manifest) with row.session_id equal to the argument."""
        _seed(engine_paths, runs_repo)

        row, state, manifest = EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert row.session_id == "paused-1"
        assert manifest == state.manifest
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.PAUSED]

    @pytest.mark.parametrize(
        ("fault", "expected_status"),
        [
            pytest.param("no-row", EngineResumeStatus.NOT_FOUND, id="not-found"),
            pytest.param("completed-row", EngineResumeStatus.WRONG_STATUS, id="wrong-status"),
            pytest.param("no-state", EngineResumeStatus.CORRUPT_STATE, id="missing-state"),
            pytest.param("corrupt-json", EngineResumeStatus.FAILED, id="corrupt-state"),
            pytest.param("deleted-snapshot", EngineResumeStatus.MISSING_SNAPSHOT, id="missing-snapshot"),
            pytest.param("invalid-snapshot-yaml", EngineResumeStatus.FAILED, id="invalid-snapshot"),
            pytest.param("paused-leaf-without-result", EngineResumeStatus.CORRUPT_STATE, id="paused-leaf-no-result"),
            pytest.param("all-nodes-terminal", EngineResumeStatus.CORRUPT_STATE, id="nothing-runnable"),
        ],
    )
    def test_invalid_run_raises_engine_resume_error_with_status(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        fault: str,
        expected_status: EngineResumeStatus,
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: each seeded fault raises EngineResumeError whose .status equals expected_status and whose message starts "Cannot resume session" (or "Session '<id>' not found." for NOT_FOUND)."""
        if fault == "no-state":
            runs_repo.create("paused-1", blueprint_name="x", blueprint_key="x", status=RunStatus.PAUSED)
        elif fault != "no-row":
            row = _seed(engine_paths, runs_repo)
            if fault == "completed-row":
                runs_repo.update_status("paused-1", RunStatus.COMPLETED)
            elif fault == "corrupt-json":
                runs_repo.save_execution_state(
                    "paused-1", "{not json", expected_revision=row.execution_state_revision or 0, next_revision=99
                )
            elif fault == "deleted-snapshot":
                snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1").unlink()
            elif fault == "invalid-snapshot-yaml":
                snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1").write_text("- a\n- list\n")
            else:
                _break_state(engine_paths, runs_repo, fault)

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert exc_info.value.status == expected_status
        prefix = "Session 'paused-1' not found." if fault == "no-row" else "Cannot resume session 'paused-1'"
        assert str(exc_info.value).startswith(prefix)

    def test_sandboxed_run_with_existing_directory_loads(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: use_sandbox=True with sandbox_id set and paths.sandbox_dir(sandbox_id) present returns the row with that sandbox_id unchanged."""
        _seed(engine_paths, runs_repo, use_sandbox=True, sandbox_id="sbx-1")
        engine_paths.sandbox_dir("sbx-1").mkdir(parents=True)

        row, _, _ = EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert row.sandbox_id == "sbx-1"

    @pytest.mark.parametrize(
        "sandbox_id", [pytest.param(None, id="no-id"), pytest.param("gone", id="deleted-directory")]
    )
    def test_sandboxed_run_without_retained_directory_raises_missing_sandbox_and_keeps_row(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, sandbox_id: str | None
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: use_sandbox=True with sandbox_id None or a missing directory raises EngineResumeError(MISSING_SANDBOX) and runs_repo.get(session_id) still returns the row."""
        _seed(engine_paths, runs_repo, use_sandbox=True, sandbox_id=sandbox_id)

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.MISSING_SANDBOX
        assert "no longer exists" in str(exc_info.value)
        assert runs_repo.get("paused-1") is not None


class EngineLoaderPausedBodyLeafTests:
    """[tier-1/integration] EngineLoader.load_for_resume: paused loop-body leaves are validated like top-level leaves."""

    def test_paused_body_leaf_with_recorded_result_loads(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused run whose loop body leaf is PAUSED with a recorded failed result returns the state with that leaf paused."""
        _seed_paused_loop(engine_paths, runs_repo)

        _, state, _ = EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        assert [leaf.state for leaf in loop.iterations[0].steps] == [NodeState.COMPLETED, NodeState.PAUSED]

    def test_paused_body_leaf_without_recorded_result_is_corrupt_state(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused body leaf whose last attempt has no result raises EngineResumeError with status CORRUPT_STATE naming the leaf id."""
        store = _seed_paused_loop(engine_paths, runs_repo)
        state = store.load().state
        assert state is not None
        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        paused = loop.iterations[0].steps[1]
        paused.attempts[-1] = paused.attempts[-1].model_copy(update={"result": None})
        assert store.save(state).ok

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.CORRUPT_STATE
        assert "'verify'" in str(exc_info.value)


class EngineLoaderCorruptLoopStateTests:
    """[tier-1/integration] EngineLoader.load_for_resume: loop structure must match the run snapshot."""

    def test_structurally_corrupt_loop_state_is_corrupt_state_on_resume(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused run whose loop iteration children differ from the snapshot body raises EngineResumeError with status CORRUPT_STATE and a message ending with the validator's text."""
        store = _seed_paused_loop(engine_paths, runs_repo)
        state = store.load().state
        assert state is not None
        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        loop.iterations[0].steps.reverse()
        assert store.save(state).ok

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.CORRUPT_STATE
        assert str(exc_info.value).endswith(
            "Loop 'fix' iteration 1 steps ['verify', 'edit'] do not match the loop body ['edit', 'verify']."
        )
