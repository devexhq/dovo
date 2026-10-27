"""Contract tests for SessionStateStore: initial tree, revisioned saves, projection recovery, and snapshot checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.engine import EngineError, SessionStateStore
from dovo.engine.models import DefinitionRef, DefinitionsManifest, LifecycleUpdate
from dovo.engine.projection import build_session_json_payload
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    SessionJsonPayload,
    StepAttemptRecord,
)
from dovo.engine.state_store import new_iteration
from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from tests.harness.sessions import SEEDED_SPAN
from tests.harness.workspace_paths import initialized_workspace_paths

SESSION_ID = "state-1"


def _blueprint() -> Blueprint:
    """Blueprint with a linear step `setup` followed by loop `fix` whose do is [edit, verify]."""
    loop = LoopStepBlock(
        id="fix",
        type="loop",
        max_iterations=3,
        until=["steps.verify.exit_code == 0"],
        on_max_iterations=FailurePolicy.PROMPT_USER,
        do=[
            StepBuilder.command("echo edit").with_id("edit").build(),
            StepBuilder.command("echo v").with_id("verify").build(),
        ],
    )
    builder = (
        BlueprintBuilder("bp").with_step(StepBuilder.command("echo setup").with_id("setup").build()).with_step(loop)
    )
    return Blueprint(builder.build())


def _write_snapshot(paths: WorkspacePaths, session_id: str) -> DefinitionsManifest:
    """Write one blueprint and one step snapshot file for the session and return the matching manifest."""
    session_dir = paths.session_dir(session_id)
    Filesystem.atomic_write_text(session_dir / "definitions" / "bp.yml", "name: bp\n")
    Filesystem.atomic_write_text(session_dir / "definitions" / "steps" / "st.yml", "id: st\n")
    return DefinitionsManifest(
        blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="a", resolved_at="2026-09-28T00:00:00+00:00"),
        steps=[DefinitionRef(ref="repo:step:st", sha="b", resolved_at="2026-09-28T00:00:00+00:00")],
    )


class _Fixture:
    """A workspace with one RUNNING row, its snapshot manifest, and a store bound to it."""

    def __init__(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        self.paths = initialized_workspace_paths(workspace)
        self.sessions = SessionsRepository(db_path=self.paths.database_file, project_id=self.paths.project_id)
        self.sessions.create(SESSION_ID, blueprint_name="bp", blueprint_key="bp")
        self.manifest = _write_snapshot(self.paths, SESSION_ID)
        self.store = SessionStateStore(self.sessions, self.paths, SESSION_ID)
        self.session_json = self.paths.session_dir(SESSION_ID) / "session.json"

    def initialized_at(self, revision: int) -> ExecutionStateTree:
        """Initialize the run, then advance it through the store to the given revision."""
        state = self.store.initialize(_blueprint(), self.manifest, [])
        for _ in range(revision):
            state = self.store.save(state, [])
        return state

    def projection(self) -> SessionJsonPayload:
        """Parse the current session.json."""
        return SessionJsonPayload.model_validate_json(self.session_json.read_text(encoding="utf-8"))

    def row(self) -> SessionRecord:
        """Return the session row."""
        row = self.sessions.get(SESSION_ID)
        assert row is not None
        return row


class NewIterationTests:
    def test_new_iteration_is_pending_with_pending_leaf_per_body_step(self) -> None:
        loop = _blueprint().steps[1]
        assert isinstance(loop, LoopStepBlock)

        iteration = new_iteration(loop, 2)

        assert iteration == ExecutionIterationRecord(
            number=2,
            state=NodeState.PENDING,
            steps=[ExecutionLeafNode(id="edit"), ExecutionLeafNode(id="verify")],
            until_passed=None,
        )

    def test_new_iteration_number_below_one_raises_validation_error(self) -> None:
        loop = _blueprint().steps[1]
        assert isinstance(loop, LoopStepBlock)

        with pytest.raises(ValidationError):
            new_iteration(loop, 0)


class SessionStateStoreInitializeTests:
    """Contract tests for SessionStateStore.initialize."""

    def test_initialize_linear_and_loop_blueprint_builds_ordered_pending_tree(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)

        warnings: list[str] = []

        state = fixture.store.initialize(_blueprint(), fixture.manifest, warnings)

        expected = ExecutionStateTree(
            manifest=fixture.manifest,
            nodes=[
                ExecutionLeafNode(id="setup"),
                ExecutionLoopNode(
                    id="fix",
                    max_iterations=3,
                    until=["steps.verify.exit_code == 0"],
                    on_max_iterations=FailurePolicy.PROMPT_USER,
                    iterations=[
                        ExecutionIterationRecord(
                            number=1,
                            steps=[ExecutionLeafNode(id="edit"), ExecutionLeafNode(id="verify")],
                        )
                    ],
                ),
            ],
        )
        assert state == expected
        assert warnings == []
        assert fixture.projection().nodes == expected.nodes
        row = fixture.sessions.get(SESSION_ID)
        assert row is not None
        assert row.execution_state_revision == 0
        assert ExecutionStateTree.model_validate_json(row.execution_state_json or "") == expected
        assert all(node.state == NodeState.PENDING for node in expected.nodes)

    def test_initialize_missing_row_raises_session_not_found(self, tmp_path: Path) -> None:
        """Raise EngineError(SESSION_NOT_FOUND, "Session 'unknown' not found.") and write no session.json."""
        fixture = _Fixture(tmp_path)
        store = SessionStateStore(fixture.sessions, fixture.paths, "unknown")

        with pytest.raises(EngineError) as raised:
            store.initialize(_blueprint(), fixture.manifest, [])

        assert raised.value.error_code is ErrorCode.SESSION_NOT_FOUND
        assert raised.value.message == "Session 'unknown' not found."
        assert not fixture.paths.session_dir("unknown").joinpath("session.json").exists()


class SessionStateStoreSaveTests:
    """Contract tests for SessionStateStore.save."""

    def test_save_loaded_state_persists_next_revision_in_row_and_projection(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(4)
        loaded = fixture.store.load([])

        saved = fixture.store.save(loaded, [])

        row = fixture.sessions.get(SESSION_ID)
        assert row is not None
        assert saved.revision == 5
        assert row.execution_state_revision == 5
        assert fixture.projection().revision == 5

    def test_save_stale_revision_raises_conflict_and_leaves_row_and_projection(self, tmp_path: Path) -> None:
        """Raise EngineError(SESSION_STATE_REVISION_CONFLICT, ...); row and session.json stay at the winner's revision 5."""
        fixture = _Fixture(tmp_path)
        stale = fixture.initialized_at(4)
        fixture.store.save(stale, [])
        before = fixture.sessions.get(SESSION_ID)
        assert before is not None

        with pytest.raises(EngineError) as raised:
            fixture.store.save(stale, [])

        after = fixture.sessions.get(SESSION_ID)
        assert raised.value.error_code is ErrorCode.SESSION_STATE_REVISION_CONFLICT
        assert raised.value.message == f"Execution state for session '{SESSION_ID}' was changed by another writer."
        assert after is not None
        assert after.execution_state_json == before.execution_state_json
        assert after.execution_state_revision == 5
        assert fixture.projection().revision == 5

    def test_save_missing_row_raises_session_not_found(self, tmp_path: Path) -> None:
        """Raise EngineError(SESSION_NOT_FOUND, "Session 'unknown' not found.") and write no session.json."""
        fixture = _Fixture(tmp_path)
        state = ExecutionStateTree(manifest=fixture.manifest)
        store = SessionStateStore(fixture.sessions, fixture.paths, "unknown")

        with pytest.raises(EngineError) as raised:
            store.save(state, [])

        assert raised.value.error_code is ErrorCode.SESSION_NOT_FOUND
        assert raised.value.message == "Session 'unknown' not found."
        assert not fixture.paths.session_dir("unknown").joinpath("session.json").exists()

    def test_save_terminal_status_updates_row_and_session_json_in_same_commit(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(0)

        fixture.store.save(
            state,
            [],
            LifecycleUpdate(
                run_status=SessionStatus.FAILED, error_message="boom", worktree_id="dovo_1", worktree_kept=True
            ),
        )

        row = fixture.row()
        lifecycle = fixture.projection().lifecycle
        assert row.status == SessionStatus.FAILED
        assert row.execution_state_revision == 1
        assert lifecycle.status == SessionStatus.FAILED
        assert lifecycle.error_message == "boom"
        assert lifecycle.started_at == row.started_at
        assert lifecycle.completed_at == row.completed_at
        assert row.completed_at is not None
        assert lifecycle.worktree_id == "dovo_1"
        assert lifecycle.worktree_kept is True
        assert fixture.projection().revision == row.execution_state_revision


_CORRUPT = f"Execution state for session '{SESSION_ID}' is corrupt."
_FAULT_MESSAGES = {
    "no-state": f"Session '{SESSION_ID}' has no execution state.",
    "invalid-json": _CORRUPT,
    "revision-mismatch": _CORRUPT,
    "pre-span-attempt": _CORRUPT,
    "newer-projection": f"session.json for session '{SESSION_ID}' is at revision 3, newer than the database revision 2.",
}


def _pre_span_document(manifest: DefinitionsManifest) -> str:
    """Return a revision 1 tree whose attempt carries the legacy started_at field in place of its span."""
    leaf = ExecutionLeafNode(id="setup", attempts=[StepAttemptRecord(number=1, span=SEEDED_SPAN)])
    base = ExecutionStateTree(revision=1, manifest=manifest, nodes=[leaf]).model_dump(mode="json")
    attempt = base["nodes"][0]["attempts"][0]
    del attempt["span"]
    attempt.update({"started_at": "2026-09-28T00:00:00+00:00", "completed_at": None})
    return json.dumps(base)


def _break_store(fixture: _Fixture, fault: str) -> tuple[SessionStateStore, str]:
    """Put the fixture's run into the fault state and return the store to load and the expected message."""
    if fault == "unknown-row":
        return SessionStateStore(fixture.sessions, fixture.paths, "unknown"), "Session 'unknown' not found."
    if fault in ("invalid-json", "revision-mismatch", "pre-span-attempt"):
        documents = {
            "invalid-json": ("{not json", 0),
            "revision-mismatch": (ExecutionStateTree(revision=1, manifest=fixture.manifest).model_dump_json(), 0),
            "pre-span-attempt": (_pre_span_document(fixture.manifest), 1),
        }
        document, revision = documents[fault]
        fixture.sessions.save_execution_state(SESSION_ID, document, expected_revision=0, next_revision=revision)
    elif fault == "missing-snapshot":
        fixture.initialized_at(0)
        step_snapshot = fixture.paths.session_dir(SESSION_ID) / "definitions" / "steps" / "st.yml"
        step_snapshot.unlink()
        return fixture.store, f"Definition snapshot file missing: {step_snapshot}"
    elif fault == "newer-projection":
        state = fixture.initialized_at(2)
        newer = build_session_json_payload(state.model_copy(update={"revision": 3}), fixture.row())
        fixture.session_json.write_text(newer.model_dump_json(), encoding="utf-8")
    return fixture.store, _FAULT_MESSAGES[fault]


class SessionStateStoreLoadTests:
    """Contract tests for SessionStateStore.load."""

    @pytest.mark.parametrize("projection", ["missing", "corrupt", "older"])
    def test_load_recovers_projection_from_database(self, tmp_path: Path, projection: str) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)
        if projection == "missing":
            fixture.session_json.unlink()
        elif projection == "corrupt":
            fixture.session_json.write_text("{not json", encoding="utf-8")
        else:
            older = build_session_json_payload(state.model_copy(update={"revision": 1}), fixture.row())
            fixture.session_json.write_text(older.model_dump_json(), encoding="utf-8")

        loaded = fixture.store.load([])

        assert loaded == state
        assert fixture.projection() == build_session_json_payload(state, fixture.row())

    def test_load_rewrites_projection_when_row_lifecycle_changed_without_revision_bump(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(2)
        fixture.sessions.update_status(SESSION_ID, SessionStatus.FAILED, error_message="stale")

        fixture.store.load([])

        lifecycle = fixture.projection().lifecycle
        assert lifecycle.status == SessionStatus.FAILED
        assert lifecycle.error_message == "stale"

    @pytest.mark.parametrize(
        ("fault", "code"),
        [
            pytest.param("unknown-row", ErrorCode.SESSION_NOT_FOUND, id="unknown-row"),
            pytest.param("no-state", ErrorCode.SESSION_STATE_MISSING, id="no-state"),
            pytest.param("invalid-json", ErrorCode.SESSION_STATE_CORRUPT, id="invalid-json"),
            pytest.param("revision-mismatch", ErrorCode.SESSION_STATE_CORRUPT, id="revision-mismatch"),
            pytest.param("pre-span-attempt", ErrorCode.SESSION_STATE_CORRUPT, id="pre-span-attempt"),
            pytest.param("missing-snapshot", ErrorCode.SESSION_SNAPSHOT_MISSING, id="missing-snapshot"),
            pytest.param("newer-projection", ErrorCode.SESSION_STATE_INCONSISTENT, id="newer-projection"),
        ],
    )
    def test_load_failure_raises_engine_error_with_code_and_message(
        self, tmp_path: Path, fault: str, code: ErrorCode
    ) -> None:
        """Raise EngineError with the table's error_code and exact message, leaving the row unchanged and writing no session.json."""
        fixture = _Fixture(tmp_path)
        store, message = _break_store(fixture, fault)
        projection = store.paths.session_dir(store.session_id) / "session.json"
        row_before = fixture.sessions.get(store.session_id)
        projection_before = projection.read_bytes() if projection.exists() else None

        with pytest.raises(EngineError) as raised:
            store.load([])

        assert raised.value.error_code is code
        assert raised.value.message == message
        assert fixture.sessions.get(store.session_id) == row_before
        assert (projection.read_bytes() if projection.exists() else None) == projection_before

    def test_load_returns_validated_state_when_projection_matches(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)
        warnings: list[str] = []

        loaded = fixture.store.load(warnings)

        assert loaded == state
        assert warnings == []


class SessionStateStoreProjectionWarningTests:
    @pytest.mark.parametrize("operation", ["load", "initialize", "save"])
    def test_projection_write_failure_succeeds_and_appends_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
    ) -> None:
        """With Filesystem.atomic_write_text raising OSError("disk full"), return the tree and append exactly the projection warning to warnings."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(1) if operation != "initialize" else None
        fixture.session_json.unlink(missing_ok=True)

        def fail_write(_path: Path, _text: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_text", fail_write)
        warnings: list[str] = []

        if state is None:
            returned = fixture.store.initialize(_blueprint(), fixture.manifest, warnings)
        elif operation == "save":
            returned = fixture.store.save(state, warnings)
        else:
            returned = fixture.store.load(warnings)

        assert returned == fixture.store.load([])
        assert warnings == [
            "Failed to write session.json projection: disk full; it will be regenerated on the next load."
        ]
