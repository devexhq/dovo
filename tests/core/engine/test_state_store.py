"""Contract tests for RunStateStore: initial tree, revisioned saves, projection recovery, and snapshot checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from worktree.common.filesystem import Filesystem
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.models import FailurePolicy
from worktree.core.catalog.blueprint import Blueprint
from worktree.core.catalog.definitions import LoopStepBlock
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine import RunStateStore
from worktree.core.engine.models import DefinitionRef, DefinitionsManifest
from worktree.core.engine.projection import build_run_json_payload
from worktree.core.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    NodeState,
    RunJsonPayload,
    RunStateLoadStatus,
    RunStateWriteStatus,
)
from worktree.core.engine.state_store import new_iteration
from worktree.core.project.services.storage import resolve_workspace_paths

SESSION_ID = "state-1"


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


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
        self.paths = _paths_for(workspace)
        self.runs = RunsRepository(db_path=self.paths.database_file, project_id=self.paths.project_id)
        self.runs.create(SESSION_ID, blueprint_name="bp", blueprint_key="bp")
        self.manifest = _write_snapshot(self.paths, SESSION_ID)
        self.store = RunStateStore(self.runs, self.paths, SESSION_ID)
        self.run_json = self.paths.session_dir(SESSION_ID) / "run.json"

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

    def projection(self) -> RunJsonPayload:
        """Parse the current run.json."""
        return RunJsonPayload.model_validate_json(self.run_json.read_text(encoding="utf-8"))

    def row(self) -> RunRecord:
        """Return the run row."""
        row = self.runs.get(SESSION_ID)
        assert row is not None
        return row


class NewIterationTests:
    """[tier-1/unit] new_iteration: the single builder for seeded and repeated loop iterations."""

    def test_new_iteration_is_pending_with_pending_leaf_per_body_step(self) -> None:
        """[tier-1/unit] new_iteration: loop.do [edit, verify], number 2 -> ExecutionIterationRecord(number=2, state=PENDING, steps=[ExecutionLeafNode(id="edit"), ExecutionLeafNode(id="verify")], until_passed=None)."""
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
        """[tier-1/unit] new_iteration: number 0 raises pydantic.ValidationError."""
        loop = _blueprint().steps[1]
        assert isinstance(loop, LoopStepBlock)

        with pytest.raises(ValidationError):
            new_iteration(loop, 0)


class RunStateStoreInitializeTests:
    """Contract tests for RunStateStore.initialize."""

    def test_initialize_linear_and_loop_blueprint_builds_ordered_pending_tree(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.initialize: steps [setup, loop fix(do=[edit, verify], max_iterations=3, until=[...], on_max_iterations=prompt_user)] yield revision 0, nodes [setup, fix] all PENDING, and fix carries the loop config plus one iteration numbered 1 with steps [edit, verify]."""
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
        assert result.status == RunStateWriteStatus.OK
        assert result.state == expected
        assert fixture.projection().nodes == expected.nodes
        row = fixture.runs.get(SESSION_ID)
        assert row is not None
        assert row.execution_state_revision == 0
        assert ExecutionStateTree.model_validate_json(row.execution_state_json or "") == expected
        assert all(node.state == NodeState.PENDING for node in expected.nodes)

    def test_initialize_missing_row_returns_not_found(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.initialize: a session id with no run row returns NOT_FOUND with state None and writes no run.json."""
        fixture = _Fixture(tmp_path)
        store = RunStateStore(fixture.runs, fixture.paths, "unknown")

        result = store.initialize(_blueprint(), fixture.manifest)

        assert result.status == RunStateWriteStatus.NOT_FOUND
        assert result.state is None
        assert not fixture.paths.session_dir("unknown").joinpath("run.json").exists()


class RunStateStoreSaveTests:
    """Contract tests for RunStateStore.save."""

    def test_save_loaded_state_persists_next_revision_in_row_and_projection(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.save: a state loaded at revision 4 saves to revision 5 — row.execution_state_revision == 5, run.json carries revision 5, result.status is OK, result.state.revision == 5."""
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(4)
        loaded = fixture.store.load()
        assert loaded.state is not None

        result = fixture.store.save(loaded.state)

        row = fixture.runs.get(SESSION_ID)
        assert row is not None
        assert result.status == RunStateWriteStatus.OK
        assert result.state is not None
        assert result.state.revision == 5
        assert row.execution_state_revision == 5
        assert fixture.projection().revision == 5

    def test_save_stale_revision_returns_conflict_and_leaves_row_and_projection(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.save: a state at revision 4 when the row holds 5 returns REVISION_CONFLICT with state None; row JSON and run.json still carry revision 5."""
        fixture = _Fixture(tmp_path)
        stale = fixture.initialized_at(4)
        assert fixture.store.save(stale).ok
        before = fixture.runs.get(SESSION_ID)
        assert before is not None

        result = fixture.store.save(stale)

        after = fixture.runs.get(SESSION_ID)
        assert result.status == RunStateWriteStatus.REVISION_CONFLICT
        assert result.state is None
        assert after is not None
        assert after.execution_state_json == before.execution_state_json
        assert after.execution_state_revision == 5
        assert fixture.projection().revision == 5

    def test_save_missing_row_returns_not_found(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.save: a session id with no run row returns NOT_FOUND and writes no run.json."""
        fixture = _Fixture(tmp_path)
        state = ExecutionStateTree(manifest=fixture.manifest)
        store = RunStateStore(fixture.runs, fixture.paths, "unknown")

        result = store.save(state)

        assert result.status == RunStateWriteStatus.NOT_FOUND
        assert not fixture.paths.session_dir("unknown").joinpath("run.json").exists()

    def test_save_projection_write_failure_keeps_database_state_and_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] RunStateStore.save: Filesystem.atomic_write_text raising OSError still returns OK, row.execution_state_revision advanced, and result.warnings has one entry containing 'will be regenerated on the next load'."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(0)

        def fail_write(_path: Path, _text: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_text", fail_write)

        result = fixture.store.save(state)

        row = fixture.runs.get(SESSION_ID)
        assert row is not None
        assert result.status == RunStateWriteStatus.OK
        assert row.execution_state_revision == 1
        assert len(result.warnings) == 1
        assert "will be regenerated on the next load" in result.warnings[0]

        monkeypatch.undo()
        assert fixture.store.load().ok
        assert fixture.projection().revision == 1

    def test_save_with_run_status_updates_lifecycle_in_same_commit(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.save: run_status=COMPLETED and error_message=None leaves the row COMPLETED with completed_at set alongside the new revision."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(0)

        fixture.store.save(state, run_status=RunStatus.COMPLETED, error_message=None)

        row = fixture.runs.get(SESSION_ID)
        assert row is not None
        assert row.status == RunStatus.COMPLETED
        assert row.completed_at is not None
        assert row.execution_state_revision == 1

    def test_save_terminal_status_writes_row_equal_lifecycle_to_run_json(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.save: run_status=FAILED, error_message='boom', sandbox_id='sbx-1', sandbox_kept=True leaves run.json.lifecycle equal to the row's status, error_message, started_at, completed_at, sandbox_id, and sandbox_kept, and run.json.revision == row.execution_state_revision."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(0)

        fixture.store.save(
            state, run_status=RunStatus.FAILED, error_message="boom", sandbox_id="sbx-1", sandbox_kept=True
        )

        row = fixture.row()
        lifecycle = fixture.projection().lifecycle
        assert lifecycle.status == RunStatus.FAILED
        assert lifecycle.error_message == "boom"
        assert lifecycle.started_at == row.started_at
        assert lifecycle.completed_at == row.completed_at
        assert row.completed_at is not None
        assert lifecycle.sandbox_id == "sbx-1"
        assert lifecycle.sandbox_kept is True
        assert fixture.projection().revision == row.execution_state_revision


class RunStateStoreLoadTests:
    """Contract tests for RunStateStore.load."""

    @pytest.mark.parametrize("projection", ["missing", "corrupt", "older"])
    def test_load_recovers_projection_from_database(self, tmp_path: Path, projection: str) -> None:
        """[tier-1/integration] RunStateStore.load: a database state at revision 2 with run.json missing, containing '{not json', or carrying revision 1 returns OK and rewrites run.json to the revision 2 state."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)
        if projection == "missing":
            fixture.run_json.unlink()
        elif projection == "corrupt":
            fixture.run_json.write_text("{not json", encoding="utf-8")
        else:
            older = build_run_json_payload(state.model_copy(update={"revision": 1}), fixture.row())
            fixture.run_json.write_text(older.model_dump_json(), encoding="utf-8")

        result = fixture.store.load()

        assert result.status == RunStateLoadStatus.OK
        assert result.state == state
        assert fixture.projection() == build_run_json_payload(state, fixture.row())

    def test_load_newer_projection_returns_inconsistent_and_leaves_row(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.load: run.json at revision 3 over a row at revision 2 returns INCONSISTENT_PROJECTION with state None; the row's JSON and revision are unchanged."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)
        before = fixture.runs.get(SESSION_ID)
        assert before is not None
        newer = build_run_json_payload(state.model_copy(update={"revision": 3}), before)
        fixture.run_json.write_text(newer.model_dump_json(), encoding="utf-8")

        result = fixture.store.load()

        after = fixture.runs.get(SESSION_ID)
        assert result.status == RunStateLoadStatus.INCONSISTENT_PROJECTION
        assert result.state is None
        assert after is not None
        assert after.execution_state_json == before.execution_state_json
        assert after.execution_state_revision == 2

    def test_load_rewrites_projection_when_row_lifecycle_changed_without_revision_bump(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.load: after runs.update_status(FAILED, error_message='stale') at an unchanged revision, load returns OK and run.json.lifecycle.status == FAILED with error_message 'stale'."""
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(2)
        fixture.runs.update_status(SESSION_ID, RunStatus.FAILED, error_message="stale")

        result = fixture.store.load()

        lifecycle = fixture.projection().lifecycle
        assert result.status == RunStateLoadStatus.OK
        assert lifecycle.status == RunStatus.FAILED
        assert lifecycle.error_message == "stale"

    def test_load_returns_validated_state_when_projection_matches(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.load: a row at revision 2 with a matching run.json returns OK with state equal to the saved tree and no warnings."""
        fixture = _Fixture(tmp_path)
        state = fixture.initialized_at(2)

        result = fixture.store.load()

        assert result.status == RunStateLoadStatus.OK
        assert result.state == state
        assert result.warnings == []

    @pytest.mark.parametrize("corruption", ["invalid_json", "revision_mismatch"])
    def test_load_corrupt_state_returns_corrupt_state_without_writing_projection(
        self, tmp_path: Path, corruption: str
    ) -> None:
        """[tier-1/integration] RunStateStore.load: unparseable execution_state_json, or a document whose revision differs from the column, returns CORRUPT_STATE with state None and creates no run.json."""
        fixture = _Fixture(tmp_path)
        document = (
            "{not json"
            if corruption == "invalid_json"
            else ExecutionStateTree(revision=1, manifest=fixture.manifest).model_dump_json()
        )
        fixture.runs.save_execution_state(SESSION_ID, document, expected_revision=0, next_revision=0)

        result = fixture.store.load()

        assert result.status == RunStateLoadStatus.CORRUPT_STATE
        assert result.state is None
        assert not fixture.run_json.exists()

    def test_load_missing_snapshot_returns_missing_snapshot_naming_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.load: a manifest step whose definitions/steps/<key>.yml was deleted returns MISSING_SNAPSHOT with state None and errors[0] containing that path."""
        fixture = _Fixture(tmp_path)
        fixture.initialized_at(0)
        step_snapshot = fixture.paths.session_dir(SESSION_ID) / "definitions" / "steps" / "st.yml"
        step_snapshot.unlink()

        result = fixture.store.load()

        assert result.status == RunStateLoadStatus.MISSING_SNAPSHOT
        assert result.state is None
        assert str(step_snapshot) in result.errors[0]

    def test_load_row_without_state_returns_missing_state(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.load: a row whose execution_state_json is None returns MISSING_STATE with state None."""
        fixture = _Fixture(tmp_path)

        result = fixture.store.load()

        assert result.status == RunStateLoadStatus.MISSING_STATE
        assert result.state is None

    def test_load_unknown_session_returns_not_found(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.load: a session id with no row returns NOT_FOUND."""
        fixture = _Fixture(tmp_path)

        result = RunStateStore(fixture.runs, fixture.paths, "unknown").load()

        assert result.status == RunStateLoadStatus.NOT_FOUND


class RunStateStoreRegenerateProjectionTests:
    """Contract tests for RunStateStore.regenerate_projection."""

    def test_regenerate_projection_writes_payload_json_and_returns_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] RunStateStore.regenerate_projection: returns <session-dir>/run.json whose contents equal build_run_json_payload(state, row).model_dump_json(indent=2)."""
        fixture = _Fixture(tmp_path)
        state = ExecutionStateTree(revision=3, manifest=fixture.manifest, nodes=[ExecutionLeafNode(id="setup")])

        row = fixture.row()

        path = fixture.store.regenerate_projection(state, row)

        assert path == fixture.run_json
        assert path.read_text(encoding="utf-8") == build_run_json_payload(state, row).model_dump_json(indent=2)

    def test_regenerate_projection_unwritable_directory_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] RunStateStore.regenerate_projection: Filesystem.atomic_write_text raising OSError propagates OSError to the caller."""
        fixture = _Fixture(tmp_path)

        def fail_write(_path: Path, _text: str) -> None:
            raise OSError("read-only")

        monkeypatch.setattr(Filesystem, "atomic_write_text", fail_write)

        with pytest.raises(OSError, match="read-only"):
            fixture.store.regenerate_projection(ExecutionStateTree(manifest=fixture.manifest), fixture.row())
