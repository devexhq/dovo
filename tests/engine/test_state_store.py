"""Contract tests for SessionStateStore: initial tree, revisioned saves, projection recovery, and snapshot checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.engine import SessionStateStore
from dovo.engine.models import DefinitionRef, DefinitionsManifest
from dovo.engine.projection import build_session_json_payload
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    SessionJsonPayload,
    SessionStateLoadStatus,
    SessionStateWriteStatus,
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
        result = self.store.initialize(_blueprint(), self.manifest)
        assert result.state is not None
        state = result.state
        for _ in range(revision):
            saved = self.store.save(state)
            assert saved.state is not None
            state = saved.state
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

        result = fixture.store.initialize(_blueprint(), fixture.manifest)

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
        assert result.status == SessionStateWriteStatus.OK
        assert result.state == expected
        assert fixture.projection().nodes == expected.nodes
        row = fixture.sessions.get(SESSION_ID)
        assert row is not None
        assert row.execution_state_revision == 0
        assert ExecutionStateTree.model_validate_json(row.execution_state_json or "") == expected
        assert all(node.state == NodeState.PENDING for node in expected.nodes)

    def test_initialize_missing_row_returns_not_found(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        store = SessionStateStore(fixture.sessions, fixture.paths, "unknown")

        result = store.initialize(_blueprint(), fixture.manifest)

        assert result.status == SessionStateWriteStatus.NOT_FOUND
        assert result.state is None
        assert not fixture.paths.session_dir("unknown").joinpath("session.json").exists()


class SessionStateStoreSaveTests:
    """Contract tests for SessionStateStore.save."""

    def test_save_loaded_state_persists_next_revision_in_row_and_projection(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(4)
        loaded = fixture.store.load()
        assert loaded.state is not None

        result = fixture.store.save(loaded.state)

        row = fixture.sessions.get(SESSION_ID)
        assert row is not None
        assert result.status == SessionStateWriteStatus.OK
        assert result.state is not None
        assert result.state.revision == 5
        assert row.execution_state_revision == 5
        assert fixture.projection().revision == 5

    def test_save_stale_revision_returns_conflict_and_leaves_row_and_projection(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        stale = fixture.initialized_at(4)
        assert fixture.store.save(stale).ok
        before = fixture.sessions.get(SESSION_ID)
        assert before is not None

        result = fixture.store.save(stale)

        after = fixture.sessions.get(SESSION_ID)
        assert result.status == SessionStateWriteStatus.REVISION_CONFLICT
        assert result.state is None
        assert after is not None
        assert after.execution_state_json == before.execution_state_json
        assert after.execution_state_revision == 5
        assert fixture.projection().revision == 5

    def test_save_missing_row_returns_not_found(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        state = ExecutionStateTree(manifest=fixture.manifest)
        store = SessionStateStore(fixture.sessions, fixture.paths, "unknown")

        result = store.save(state)

        assert result.status == SessionStateWriteStatus.NOT_FOUND
        assert not fixture.paths.session_dir("unknown").joinpath("session.json").exists()

    def test_save_projection_write_failure_keeps_database_state_and_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(0)

        def fail_write(_path: Path, _text: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_text", fail_write)

        result = fixture.store.save(state)

        row = fixture.sessions.get(SESSION_ID)
        assert row is not None
        assert result.status == SessionStateWriteStatus.OK
        assert row.execution_state_revision == 1
        assert len(result.warnings) == 1
        assert "will be regenerated on the next load" in result.warnings[0]

        monkeypatch.undo()
        assert fixture.store.load().ok
        assert fixture.projection().revision == 1

    def test_save_terminal_status_updates_row_and_session_json_in_same_commit(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(0)

        fixture.store.save(
            state, run_status=SessionStatus.FAILED, error_message="boom", worktree_id="dovo_1", worktree_kept=True
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

        result = fixture.store.load()

        assert result.status == SessionStateLoadStatus.OK
        assert result.state == state
        assert fixture.projection() == build_session_json_payload(state, fixture.row())

    def test_load_newer_projection_returns_inconsistent_and_leaves_row(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)
        before = fixture.sessions.get(SESSION_ID)
        assert before is not None
        newer = build_session_json_payload(state.model_copy(update={"revision": 3}), before)
        fixture.session_json.write_text(newer.model_dump_json(), encoding="utf-8")

        result = fixture.store.load()

        after = fixture.sessions.get(SESSION_ID)
        assert result.status == SessionStateLoadStatus.INCONSISTENT_PROJECTION
        assert result.state is None
        assert after is not None
        assert after.execution_state_json == before.execution_state_json
        assert after.execution_state_revision == 2

    def test_load_rewrites_projection_when_row_lifecycle_changed_without_revision_bump(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(2)
        fixture.sessions.update_status(SESSION_ID, SessionStatus.FAILED, error_message="stale")

        result = fixture.store.load()

        lifecycle = fixture.projection().lifecycle
        assert result.status == SessionStateLoadStatus.OK
        assert lifecycle.status == SessionStatus.FAILED
        assert lifecycle.error_message == "stale"

    def test_load_returns_validated_state_when_projection_matches(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)

        result = fixture.store.load()

        assert result.status == SessionStateLoadStatus.OK
        assert result.state == state
        assert result.warnings == []

    @pytest.mark.parametrize("corruption", ["invalid_json", "revision_mismatch"])
    def test_load_corrupt_state_returns_corrupt_state_without_writing_projection(
        self, tmp_path: Path, corruption: str
    ) -> None:
        fixture = _Fixture(tmp_path)
        document = (
            "{not json"
            if corruption == "invalid_json"
            else ExecutionStateTree(revision=1, manifest=fixture.manifest).model_dump_json()
        )
        fixture.sessions.save_execution_state(SESSION_ID, document, expected_revision=0, next_revision=0)

        result = fixture.store.load()

        assert result.status == SessionStateLoadStatus.CORRUPT_STATE
        assert result.state is None
        assert not fixture.session_json.exists()

    @pytest.mark.parametrize(
        "legacy_fields",
        [
            pytest.param({"started_at": "2026-09-28T00:00:00+00:00", "completed_at": None}, id="legacy-shape"),
            pytest.param({"completed_at": "2026-09-28T00:00:01+00:00"}, id="span-plus-completed-at"),
        ],
    )
    def test_load_state_with_pre_span_attempt_fields_returns_corrupt_state(
        self, tmp_path: Path, legacy_fields: dict[str, object]
    ) -> None:
        """Return CORRUPT_STATE with no state and no session.json for a revision-matching tree whose attempt carries started_at or completed_at."""
        fixture = _Fixture(tmp_path)
        leaf = ExecutionLeafNode(id="setup", attempts=[StepAttemptRecord(number=1, span=SEEDED_SPAN)])
        base = ExecutionStateTree(revision=1, manifest=fixture.manifest, nodes=[leaf]).model_dump(mode="json")
        ExecutionStateTree.model_validate_json(json.dumps(base))
        attempt = base["nodes"][0]["attempts"][0]
        if "started_at" in legacy_fields:
            del attempt["span"]
        attempt.update(legacy_fields)
        fixture.sessions.save_execution_state(SESSION_ID, json.dumps(base), expected_revision=0, next_revision=1)

        result = fixture.store.load()

        assert result.status == SessionStateLoadStatus.CORRUPT_STATE
        assert result.state is None
        assert not fixture.session_json.exists()

    def test_load_missing_snapshot_returns_missing_snapshot_naming_path(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(0)
        step_snapshot = fixture.paths.session_dir(SESSION_ID) / "definitions" / "steps" / "st.yml"
        step_snapshot.unlink()

        result = fixture.store.load()

        assert result.status == SessionStateLoadStatus.MISSING_SNAPSHOT
        assert result.state is None
        assert str(step_snapshot) in result.errors[0]

    def test_load_row_without_state_returns_missing_state(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)

        result = fixture.store.load()

        assert result.status == SessionStateLoadStatus.MISSING_STATE
        assert result.state is None

    def test_load_unknown_session_returns_not_found(self, tmp_path: Path) -> None:
        fixture = _Fixture(tmp_path)

        result = SessionStateStore(fixture.sessions, fixture.paths, "unknown").load()

        assert result.status == SessionStateLoadStatus.NOT_FOUND
