"""Contract tests for EngineLoader: paused-run validation before coordinator dispatch."""

from __future__ import annotations

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.engine import EngineLoader, EngineResumeError, EngineResumeStatus, SessionStateStore
from dovo.engine.executors.models import StepResult
from dovo.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    NodeState,
    StepAttemptRecord,
)
from dovo.engine.writer import snapshot_blueprint_path
from tests.harness.sessions import SEEDED_FAILURE, seed_new_session, seed_paused_session

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
    sessions: SessionsRepository,
    *,
    use_worktree: bool = False,
    worktree_id: str | None = None,
) -> SessionRecord:
    return seed_paused_session(
        paths,
        sessions,
        session_id="paused-1",
        steps=_STEPS,
        paused_step_id="b",
        use_worktree=use_worktree,
        worktree_id=worktree_id,
    )


def _break_state(paths: WorkspacePaths, sessions: SessionsRepository, fault: str) -> None:
    """Corrupt the seeded paused run's state document in the way fault names."""
    store = SessionStateStore(sessions, paths, "paused-1")
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


def _seed_paused_loop(paths: WorkspacePaths, sessions: SessionsRepository) -> SessionStateStore:
    """Seed a paused run whose loop `fix` has completed body step `edit` and PAUSED body step `verify` on a failed attempt."""
    seed_new_session(paths, sessions, session_id="paused-1", steps=_LOOP_STEPS)
    row = sessions.get("paused-1")
    assert row is not None
    sessions.update_status("paused-1", SessionStatus.PAUSED)
    store = SessionStateStore(sessions, paths, "paused-1")
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
    assert store.save(state, run_status=SessionStatus.PAUSED).ok
    return store


class EngineLoaderTests:
    """[tier-1/integration] EngineLoader.load_for_resume: classify and validate a paused run before dispatch."""

    def test_paused_run_returns_row_state_and_manifest(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused row with a PAUSED leaf and intact snapshots returns (row, state, state.manifest) with row.session_id equal to the argument."""
        _seed(engine_paths, sessions_repo)

        row, state, manifest = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

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
        sessions_repo: SessionsRepository,
        fault: str,
        expected_status: EngineResumeStatus,
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: each seeded fault raises EngineResumeError whose .status equals expected_status and whose message starts "Cannot resume session" (or "Session '<id>' not found." for NOT_FOUND)."""
        if fault == "no-state":
            sessions_repo.create("paused-1", blueprint_name="x", blueprint_key="x", status=SessionStatus.PAUSED)
        elif fault != "no-row":
            row = _seed(engine_paths, sessions_repo)
            if fault == "completed-row":
                sessions_repo.update_status("paused-1", SessionStatus.COMPLETED)
            elif fault == "corrupt-json":
                sessions_repo.save_execution_state(
                    "paused-1", "{not json", expected_revision=row.execution_state_revision or 0, next_revision=99
                )
            elif fault == "deleted-snapshot":
                snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1").unlink()
            elif fault == "invalid-snapshot-yaml":
                snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1").write_text("- a\n- list\n")
            else:
                _break_state(engine_paths, sessions_repo, fault)

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.status == expected_status
        prefix = "Session 'paused-1' not found." if fault == "no-row" else "Cannot resume session 'paused-1'"
        assert str(exc_info.value).startswith(prefix)

    def test_worktree_run_with_existing_directory_loads(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: use_worktree=True with worktree_id set and paths.worktree_dir(worktree_id) present returns the row with that worktree_id unchanged."""
        _seed(engine_paths, sessions_repo, use_worktree=True, worktree_id="dovo_1")
        engine_paths.worktree_dir("dovo_1").mkdir(parents=True)

        row, _, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert row.worktree_id == "dovo_1"

    @pytest.mark.parametrize(
        "worktree_id", [pytest.param(None, id="no-id"), pytest.param("gone", id="deleted-directory")]
    )
    def test_worktree_run_without_retained_directory_raises_missing_worktree_and_keeps_row(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, worktree_id: str | None
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: use_worktree=True with worktree_id None or a missing directory raises EngineResumeError(MISSING_WORKTREE) and sessions_repo.get(session_id) still returns the row."""
        _seed(engine_paths, sessions_repo, use_worktree=True, worktree_id=worktree_id)

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.MISSING_WORKTREE
        assert "no longer exists" in str(exc_info.value)
        assert sessions_repo.get("paused-1") is not None


class EngineLoaderPausedBodyLeafTests:
    """[tier-1/integration] EngineLoader.load_for_resume: paused loop-body leaves are validated like top-level leaves."""

    def test_paused_body_leaf_with_recorded_result_loads(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused run whose loop body leaf is PAUSED with a recorded failed result returns the state with that leaf paused."""
        _seed_paused_loop(engine_paths, sessions_repo)

        _, state, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        assert [leaf.state for leaf in loop.iterations[0].steps] == [NodeState.COMPLETED, NodeState.PAUSED]

    def test_paused_body_leaf_without_recorded_result_is_corrupt_state(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused body leaf whose last attempt has no result raises EngineResumeError with status CORRUPT_STATE naming the leaf id."""
        store = _seed_paused_loop(engine_paths, sessions_repo)
        state = store.load().state
        assert state is not None
        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        paused = loop.iterations[0].steps[1]
        paused.attempts[-1] = paused.attempts[-1].model_copy(update={"result": None})
        assert store.save(state).ok

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.CORRUPT_STATE
        assert "'verify'" in str(exc_info.value)


class EngineLoaderCorruptLoopStateTests:
    """[tier-1/integration] EngineLoader.load_for_resume: loop structure must match the run snapshot."""

    def test_structurally_corrupt_loop_state_is_corrupt_state_on_resume(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused run whose loop iteration children differ from the snapshot body raises EngineResumeError with status CORRUPT_STATE and a message ending with the validator's text."""
        store = _seed_paused_loop(engine_paths, sessions_repo)
        state = store.load().state
        assert state is not None
        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        loop.iterations[0].steps.reverse()
        assert store.save(state).ok

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.CORRUPT_STATE
        assert str(exc_info.value).endswith(
            "Loop 'fix' iteration 1 steps ['verify', 'edit'] do not match the loop body ['edit', 'verify']."
        )
