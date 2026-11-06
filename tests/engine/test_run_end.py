"""Contract tests for record_run_end: the final status write of a started run."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.engine import SessionStateStore
from dovo.engine.models import RunOutcome
from dovo.engine.run_end import record_run_end
from dovo.engine.state_models import ExecutionLeafNode, ExecutionLoopNode, NodeState
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.sessions import seed_new_session

_LOOP: dict[str, object] = {
    "id": "loop",
    "type": "loop",
    "max_iterations": 2,
    "until": ["iteration.index >= 2"],
    "do": [{"id": "s", "run": "true"}],
}


def _seed_running_loop(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> SessionStateStore:
    """Seed a run whose loop, first iteration and body leaf are RUNNING, and return its state store."""
    seed_new_session(paths, sessions, session_id=session_id, steps=[_LOOP])
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load([])
    loop = state.nodes[0]
    assert isinstance(loop, ExecutionLoopNode)
    loop.state = loop.iterations[0].state = loop.iterations[0].steps[0].state = NodeState.RUNNING
    store.save(state, [])
    return store


def _seed_paused_leaf(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> SessionStateStore:
    """Seed a run whose one leaf is PAUSED, and return its state store."""
    seed_new_session(paths, sessions, session_id=session_id, steps=[{"id": "a", "run": "true"}])
    store = SessionStateStore(sessions, paths, session_id)
    state = store.load([])
    state.nodes[0].state = NodeState.PAUSED
    store.save(state, [])
    return store


def _loop_states(store: SessionStateStore) -> tuple[NodeState, NodeState, NodeState]:
    """Return the saved state of the loop, its first iteration and that iteration's body leaf."""
    loop = store.load([]).nodes[0]
    assert isinstance(loop, ExecutionLoopNode)
    return loop.state, loop.iterations[0].state, loop.iterations[0].steps[0].state


class RecordRunEndTests:
    @pytest.mark.parametrize(
        ("status", "node_state"),
        [(SessionStatus.CANCELLED, NodeState.CANCELLED), (SessionStatus.FAILED, NodeState.FAILED)],
    )
    def test_cancelled_or_failed_outcome_settles_started_nodes(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        status: SessionStatus,
        node_state: NodeState,
    ) -> None:
        """Save the row in the outcome's status and the running loop, its iteration and its body leaf in the matching node state."""
        store = _seed_running_loop(engine_paths, sessions_repo, "end-1")
        outcome = RunOutcome(status=status, errors=["stopped"], worktree_path=engine_paths.root_dir)
        warnings: list[str] = []

        record_run_end(store, outcome, warnings, [])

        row = sessions_repo.get("end-1")
        assert row is not None
        assert (row.status, row.error_message, warnings) == (status, "stopped", [])
        assert _loop_states(store) == (node_state, node_state, node_state)

    def test_paused_outcome_leaves_nodes_unchanged(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        store = _seed_paused_leaf(engine_paths, sessions_repo, "paused-1")
        outcome = RunOutcome(status=SessionStatus.PAUSED, worktree_path=engine_paths.root_dir)

        record_run_end(store, outcome, [], [])

        row = sessions_repo.get("paused-1")
        assert row is not None
        leaf = store.load([]).nodes[0]
        assert isinstance(leaf, ExecutionLeafNode)
        assert (row.status, leaf.state) == (SessionStatus.PAUSED, NodeState.PAUSED)

    def test_before_close_leaves_worktree_kept_unchanged(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """Written with closed=False, keep the row's worktree_kept; written after the close, set it from the outcome."""
        store = _seed_paused_leaf(engine_paths, sessions_repo, "kept-1")
        sessions_repo.update_status("kept-1", SessionStatus.RUNNING, worktree_kept=True)
        outcome = RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        record_run_end(store, outcome, [], [], closed=False)
        before_close = sessions_repo.get("kept-1")
        record_run_end(store, outcome, [], [])
        after_close = sessions_repo.get("kept-1")

        assert before_close is not None
        assert after_close is not None
        assert (before_close.status, before_close.worktree_kept) == (SessionStatus.COMPLETED, True)
        assert after_close.worktree_kept is False

    def test_write_failure_is_a_warning_with_its_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With the state save raising OSError("disk full"), append the status-write warning and one engine.persist record."""
        store = _seed_paused_leaf(engine_paths, sessions_repo, "disk-1")

        def _disk_full(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(SessionsRepository, "save_execution_state", _disk_full)
        warnings: list[str] = []
        diagnostics: list[DiagnosticRecord] = []

        record_run_end(
            store, RunOutcome(status=SessionStatus.FAILED, worktree_path=engine_paths.root_dir), warnings, diagnostics
        )

        assert warnings == ["Failed to update run status in database: disk full"]
        assert diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "engine.persist"
