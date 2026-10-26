"""Contract tests for Engine.run()/resume(): start persistence, dispatch through drive_run, and row finalization."""

from __future__ import annotations

import os
from pathlib import Path

import psutil
import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.session_id import SessionIdFactory
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.models import CatalogScan
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.engine import Engine, RunRequest, SessionStateStore
from dovo.engine.models import FailurePromptDecision, RunOutcome
from dovo.engine.state_models import SessionJsonPayload, SessionStateWriteResult, SessionStateWriteStatus
from dovo.engine.writer import get_session_dir
from tests.engine.conftest import StubDriveRun
from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from tests.harness.diagnostic_log import diagnostic_hint, diagnostic_record, read_single_event
from tests.harness.fakes import FakeGitRunner, SequentialIds, fixed_clock, fixed_new_id
from tests.harness.sessions import ScriptedFailurePrompter, seed_paused_session
from tests.harness.workspace_paths import initialized_workspace_paths


def _raise_db_down(*args: object, **kwargs: object) -> None:
    """Stand in for a SessionsRepository method whose database is down."""
    raise RuntimeError("db down")


def _engine(paths: WorkspacePaths, sessions: SessionsRepository, *, new_id: SessionIdFactory = fixed_new_id) -> Engine:
    return Engine(paths, db=sessions, catalog=Catalog(paths), git=GitRunner(), clock=fixed_clock, new_id=new_id)


def _catalog_blueprint(paths: WorkspacePaths, key: str, steps: list[dict[str, object]]) -> Blueprint:
    write_runnable_blueprint(paths.root_dir, key=key, steps=steps)
    return Blueprint.load(key, catalog=Catalog(paths))


class EngineRunStartFailureTests:
    @pytest.mark.parametrize(
        ("fault", "expected_error", "recorded"),
        [
            pytest.param(
                "snapshot",
                "Failed to snapshot run definitions: Catalog item 'uncataloged' not found.",
                False,
                id="snapshot",
            ),
            pytest.param("insert", "Failed to record run start in database: db down", True, id="row-insert"),
            pytest.param("initialize", "Failed to initialize run state: boom", True, id="state-initialize"),
        ],
    )
    def test_run_start_failure_returns_failed_outcome_without_executing_steps(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        fault: str,
        expected_error: str,
        recorded: bool,
    ) -> None:
        """Fail before any step runs; an unexpected start fault hints its message and writes one engine.persist record."""
        if fault == "snapshot":
            blueprint = Blueprint(
                BlueprintBuilder("uncataloged")
                .with_use_worktree(False)
                .with_step(StepBuilder.command("touch marker").with_id("a").build())
                .build()
            )
        else:
            blueprint = _catalog_blueprint(engine_paths, "start-fault", [{"id": "a", "run": "touch marker"}])

        def raise_boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("boom")

        if fault == "insert":
            monkeypatch.setattr(SessionsRepository, "create", _raise_db_down)
        if fault == "initialize":
            monkeypatch.setattr(SessionStateStore, "initialize", raise_boom)

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="start-1", use_worktree=False)
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [expected_error + (diagnostic_hint(frozen_diagnostic_clock) if recorded else "")]
        assert not (engine_paths.root_dir / "marker").exists()
        row = sessions_repo.get("start-1")
        assert (row.status if row is not None else None) == (SessionStatus.FAILED if fault == "initialize" else None)
        if recorded:
            event = read_single_event(frozen_diagnostic_clock)
            assert (event["level"], event["operation"], event["context"]) == (
                "error",
                "engine.persist",
                {"session_id": "start-1"},
            )
        else:
            assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_mark_failed_fallback_records_error_and_hints_message(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """Hint the start message and write one engine.persist record when marking the row failed raises."""
        blueprint = _catalog_blueprint(engine_paths, "mark-failed", [{"id": "a", "run": "touch marker"}])
        monkeypatch.setattr(
            SessionStateStore,
            "initialize",
            lambda *_args, **_kwargs: SessionStateWriteResult(
                status=SessionStateWriteStatus.NOT_FOUND, errors=["state invalid"]
            ),
        )
        monkeypatch.setattr(SessionsRepository, "update_status", _raise_db_down)

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="start-1", use_worktree=False)
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [f"state invalid{diagnostic_hint(frozen_diagnostic_clock)}"]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["exception_message"]) == (
            "error",
            "engine.persist",
            "db down",
        )

    def test_start_failure_unchanged_when_recorder_cannot_write(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return today's unhinted start message when <DOVO_HOME>/logs is a regular file."""
        blueprint = _catalog_blueprint(engine_paths, "no-log", [{"id": "a", "run": "touch marker"}])
        monkeypatch.setattr(SessionsRepository, "create", _raise_db_down)
        logs_dir = resolve_global_paths().logs_dir
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="start-1", use_worktree=False)
        )

        assert outcome.errors == ["Failed to record run start in database: db down"]


class EngineDispatchTests:
    def test_run_resolves_head_through_injected_git(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record ("rev-parse", "HEAD") in the FakeGitRunner's calls and hand drive_run the same git instance, seen by a local drive_run spy."""
        seen: list[object] = []

        def _drive(*args: object, **kwargs: object) -> RunOutcome:
            seen.append(kwargs["git"])
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=engine_paths.root_dir)

        monkeypatch.setattr("dovo.engine.engine.drive_run", _drive)
        fake = FakeGitRunner()
        engine = Engine(
            engine_paths,
            db=sessions_repo,
            catalog=Catalog(engine_paths),
            git=fake,
            clock=fixed_clock,
            new_id=fixed_new_id,
        )

        engine.run(_catalog_blueprint(engine_paths, "head", [{"id": "a", "run": "true"}]))

        assert ("rev-parse", "HEAD") in fake.calls
        assert len(seen) == 1
        assert seen[0] is fake

    def test_run_and_resume_both_complete_through_coordinator_and_finalize_row(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        engine = _engine(engine_paths, sessions_repo)
        fresh = engine.run(
            _catalog_blueprint(engine_paths, "fresh", [{"id": "a", "run": "true"}, {"id": "b", "run": "true"}]),
            RunRequest(session_id="fresh", use_worktree=False),
        )
        seed_paused_session(
            engine_paths,
            sessions_repo,
            session_id="paused",
            steps=[{"id": "a", "run": "true"}, {"id": "b", "run": "exit 1", "on_failure": "prompt_user"}],
            paused_step_id="b",
        )
        manifest = SessionStateStore(sessions_repo, engine_paths, "paused").load().state
        assert manifest is not None

        resumed = engine.resume("paused", failure_prompter=ScriptedFailurePrompter([FailurePromptDecision.CONTINUE]))

        assert (fresh.status, resumed.status) == (SessionStatus.COMPLETED, SessionStatus.COMPLETED)
        assert [result.step_id for result in fresh.step_results] == ["a", "b"]
        assert [(result.step_id, result.status) for result in resumed.step_results] == [
            ("a", "completed"),
            ("b", "ignored"),
        ]
        for session_id in ("fresh", "paused"):
            row = sessions_repo.get(session_id)
            assert row is not None
            assert row.status == SessionStatus.COMPLETED
            assert (row.execution_state_revision or 0) > 0
        final = SessionStateStore(sessions_repo, engine_paths, "paused").load().state
        assert final is not None
        assert final.manifest == manifest.manifest

    @pytest.mark.parametrize(
        ("entry", "requested_session_id", "expected_session_id", "expected_kinds"),
        [
            pytest.param("run", "explicit-1", "explicit-1", [], id="run-explicit-id"),
            pytest.param("run", None, "blueprint_0000abcd", ["blueprint"], id="run-generated-id"),
            pytest.param("resume", "resume-1", "resume-1", [], id="resume-id"),
        ],
    )
    def test_run_and_resume_pass_their_session_id_to_drive_run(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        entry: str,
        requested_session_id: str | None,
        expected_session_id: str,
        expected_kinds: list[str],
    ) -> None:
        """Persist and dispatch the explicit session id, or one new_id("blueprint") call's id when run gets none."""
        seen: list[str] = []

        def recording_drive_run(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
            seen.append(session_id)
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=paths.root_dir)

        stub_drive_run(recording_drive_run)
        ids = SequentialIds("0000abcd")
        engine = _engine(engine_paths, sessions_repo, new_id=ids)
        if entry == "resume":
            seed_paused_session(
                engine_paths,
                sessions_repo,
                session_id="resume-1",
                steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
                paused_step_id="a",
            )
            outcome = engine.resume("resume-1")
        else:
            blueprint = _catalog_blueprint(engine_paths, "ids", [{"id": "a", "run": "true"}])
            outcome = engine.run(blueprint, RunRequest(session_id=requested_session_id, use_worktree=False))

        assert seen == [expected_session_id]
        assert outcome.session_id == expected_session_id
        assert ids.kinds == expected_kinds
        assert sessions_repo.get(expected_session_id) is not None


class EngineResumeFinalizationTests:
    @staticmethod
    def _seed(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
        seed_paused_session(
            paths,
            sessions,
            session_id=session_id,
            steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
            paused_step_id="a",
        )

    @pytest.mark.parametrize(
        ("site", "failing_method", "expected_row"),
        [
            pytest.param("finish", "save_execution_state", (SessionStatus.RUNNING, False), id="finish"),
            pytest.param("mark-running", "update_status", (SessionStatus.COMPLETED, True), id="mark-running"),
        ],
    )
    def test_status_write_failure_records_error_and_hints_warning(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        stub_drive_run: StubDriveRun,
        frozen_diagnostic_clock: Path,
        site: str,
        failing_method: str,
        expected_row: tuple[SessionStatus, bool],
    ) -> None:
        """Keep resuming, end warnings with the hinted status-write failure and write one engine.persist record."""
        self._seed(engine_paths, sessions_repo, site)
        stub_drive_run(None)

        def disk_full(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(SessionsRepository, failing_method, disk_full)

        outcome = _engine(engine_paths, sessions_repo).resume(site)

        assert outcome.warnings == [
            f"Failed to update run status in database: disk full{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        row = sessions_repo.get(site)
        assert row is not None
        assert (row.status, row.completed_at is not None) == expected_row
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "error",
            "engine.persist",
            {"session_id": site},
        )


class EngineProcessIdentityTests:
    @pytest.mark.parametrize("entry", ["run", "resume"])
    @pytest.mark.parametrize(
        ("create_time", "expected"),
        [
            pytest.param(1792296249.25, "2026-10-18T04:04:09.250000+00:00", id="readable"),
            pytest.param(psutil.AccessDenied(), None, id="unreadable"),
        ],
    )
    def test_run_and_resume_record_pid_with_its_creation_time(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        monkeypatch: pytest.MonkeyPatch,
        entry: str,
        create_time: float | Exception,
        expected: str | None,
    ) -> None:
        def _create_time(_process: psutil.Process) -> float:
            if isinstance(create_time, Exception):
                raise create_time
            return create_time

        monkeypatch.setattr(psutil.Process, "create_time", _create_time)
        stub_drive_run(None)
        engine = _engine(engine_paths, sessions_repo)
        if entry == "resume":
            seed_paused_session(
                engine_paths,
                sessions_repo,
                session_id="identity",
                steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
                paused_step_id="a",
            )
            outcome = engine.resume("identity")
        else:
            blueprint = _catalog_blueprint(engine_paths, "identity", [{"id": "a", "run": "true"}])
            outcome = engine.run(blueprint, RunRequest(session_id="identity", use_worktree=False))

        assert outcome.status == SessionStatus.COMPLETED
        row = sessions_repo.get("identity")
        assert row is not None
        assert (row.pid, row.pid_started_at) == (os.getpid(), expected)


class EngineRunSnapshotsDefinitionsTests:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_snapshot_failure_carries_catalog_record_in_outcome(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With the uses: step file unreadable, return a FAILED outcome whose errors hold 'Failed to snapshot run definitions: <message>' without the line and whose diagnostics hold the catalog.show record."""
        write_runnable_step(
            engine_paths.root_dir, key="locked-step", definition={"id": "locked-step", "run": "echo hi"}
        )
        blueprint = _catalog_blueprint(engine_paths, "locked-task", [{"id": "s1", "uses": "locked-step"}])
        # Keep the index from the load above, so the step stays indexed after its file becomes unreadable.
        monkeypatch.setattr(
            "dovo.core.catalog.catalog.scan_and_index_catalog",
            lambda paths: CatalogScan(records=(), skipped_files=()),
        )
        step_file = engine_paths.catalog_dir / "steps" / "locked-step.yml"
        step_file.chmod(0o000)
        try:
            outcome = _engine(engine_paths, sessions_repo).run(
                blueprint, RunRequest(session_id="snap-1", use_worktree=False)
            )
        finally:
            step_file.chmod(0o644)

        event = read_single_event(frozen_diagnostic_clock)
        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [
            "Failed to snapshot run definitions: Failed to read file for catalog blueprint 'locked-step': "
            f"{event['exception_message']}"
        ]
        assert outcome.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert event["operation"] == "catalog.show"

    def test_run_writes_snapshot_files_and_records_manifest_in_state(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        write_runnable_step(
            engine_paths.root_dir, key="lint-check", definition={"id": "lint-check", "run": "echo lint"}
        )
        blueprint = _catalog_blueprint(engine_paths, "snap-task", [{"id": "s1", "uses": "lint-check"}])
        stub_drive_run(None)

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="snap-1", use_worktree=False)
        )

        assert outcome.status == SessionStatus.COMPLETED
        session_dir = get_session_dir(engine_paths, "snap-1")
        assert (session_dir / "definitions" / "snap-task.yml").is_file()
        assert (session_dir / "definitions" / "steps" / "lint-check.yml").is_file()
        loaded = SessionStateStore(sessions_repo, engine_paths, "snap-1").load()
        assert loaded.state is not None
        assert loaded.state.manifest.blueprint.ref == "repo:blueprint:snap-task"
        assert [ref.ref for ref in loaded.state.manifest.steps] == ["repo:step:lint-check"]

    def test_run_persists_initial_state_and_projection_before_first_step(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        blueprint = _catalog_blueprint(
            engine_paths, "init-task", [{"id": "s1", "run": "echo one"}, {"id": "s2", "run": "echo two"}]
        )
        observed: dict[str, object] = {}

        def observing_drive_run(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
            row = sessions.get(session_id)
            assert row is not None
            loaded = SessionStateStore(sessions, paths, session_id).load()
            assert loaded.state is not None
            observed["status"] = row.status
            observed["revision"] = row.execution_state_revision
            observed["nodes"] = [node.id for node in loaded.state.nodes]
            observed["projection"] = SessionJsonPayload.model_validate_json(
                (paths.session_dir(session_id) / "session.json").read_text(encoding="utf-8")
            ).nodes
            observed["state"] = loaded.state.nodes
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=paths.root_dir)

        stub_drive_run(observing_drive_run)

        _engine(engine_paths, sessions_repo).run(blueprint, RunRequest(session_id="init-1", use_worktree=False))

        assert observed["status"] == SessionStatus.RUNNING
        assert observed["revision"] == 0
        assert observed["nodes"] == ["s1", "s2"]
        assert observed["projection"] == observed["state"]

    def test_run_finalizes_row_with_revision_one_and_completed_status(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        blueprint = _catalog_blueprint(engine_paths, "final-task", [{"id": "s1", "run": "echo one"}])
        stub_drive_run(None)

        _engine(engine_paths, sessions_repo).run(blueprint, RunRequest(session_id="final-1", use_worktree=False))

        row = sessions_repo.get("final-1")
        assert row is not None
        assert row.status == SessionStatus.COMPLETED
        assert row.completed_at is not None
        assert row.execution_state_revision == 1

    def test_resume_finalization_bumps_the_state_revision_once(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        seed_paused_session(
            engine_paths,
            sessions_repo,
            session_id="task_defs",
            steps=[
                {"id": "setup", "run": "echo setup"},
                {"id": "publish", "run": "exit 1", "on_failure": "prompt_user"},
            ],
            paused_step_id="publish",
        )
        before = SessionStateStore(sessions_repo, engine_paths, "task_defs").load().state
        assert before is not None
        stub_drive_run(None)

        _engine(engine_paths, sessions_repo).resume("task_defs")

        after = SessionStateStore(sessions_repo, engine_paths, "task_defs").load().state
        assert after is not None
        assert after.revision == before.revision + 1


class EngineRunConfigPersistenceTests:
    @pytest.mark.parametrize(
        ("keep", "discard"), [pytest.param(True, False, id="keep"), pytest.param(False, True, id="discard")]
    )
    def test_run_persists_resolved_configuration_on_row(
        self, tmp_path: Path, stub_drive_run: StubDriveRun, keep: bool, discard: bool
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "git-workspace").with_git().with_database().build()
        paths = initialized_workspace_paths(workspace)
        sessions = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        write_runnable_blueprint(workspace, key="cfg", steps=[{"id": "s1", "run": "echo hi"}])
        blueprint = Blueprint(
            BlueprintBuilder("cfg")
            .with_use_worktree(False)
            .with_input("env", required=True)
            .with_step(StepBuilder.command("echo hi").with_id("s1").build())
            .build()
        )
        stub_drive_run(None)

        _engine(paths, sessions).run(
            blueprint,
            RunRequest(
                session_id="cfg-1",
                inputs={"env": "prod"},
                use_worktree=False,
                keep=keep,
                discard=discard,
                agent="claude",
                auto_apply=True,
            ),
        )

        row = sessions.get("cfg-1")
        assert row is not None
        assert (
            row.use_worktree,
            row.keep,
            row.discard,
            row.agent,
            row.auto_apply,
            row.inputs_json,
            row.blueprint_tier,
            row.commit_sha,
        ) == (False, keep, discard, "claude", True, '{"env": "prod"}', "repo", GitRunner().rev_parse(workspace))

    def test_run_outside_git_repository_stores_null_commit_sha(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        blueprint = _catalog_blueprint(engine_paths, "nogit-task", [{"id": "s1", "run": "echo one"}])
        stub_drive_run(None)

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="nogit-1", use_worktree=False)
        )

        row = sessions_repo.get("nogit-1")
        assert row is not None
        assert outcome.status == SessionStatus.COMPLETED
        assert row.commit_sha is None

    @pytest.mark.parametrize(
        ("outcome_status", "outcome_worktree_id"),
        [
            pytest.param(SessionStatus.COMPLETED, "dovo_1", id="completed-with-worktree"),
            pytest.param(SessionStatus.PAUSED, "dovo_2", id="paused-with-worktree"),
            pytest.param(SessionStatus.COMPLETED, None, id="completed-without-worktree"),
        ],
    )
    def test_run_records_outcome_worktree_id_on_row(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        outcome_status: SessionStatus,
        outcome_worktree_id: str | None,
    ) -> None:
        blueprint = _catalog_blueprint(engine_paths, "worktree-task", [{"id": "s1", "run": "echo one"}])
        stub_drive_run(
            lambda paths, sessions, session_id: RunOutcome(
                status=outcome_status, worktree_path=paths.root_dir, worktree_id=outcome_worktree_id
            )
        )

        _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="worktree-run", use_worktree=True, keep=True)
        )

        row = sessions_repo.get("worktree-run")
        assert row is not None
        assert row.status == outcome_status
        assert row.worktree_id == outcome_worktree_id


class EngineRunProjectionTests:
    @pytest.mark.parametrize("keep", [False, True])
    def test_run_terminal_row_and_session_json_agree(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        keep: bool,
    ) -> None:
        blueprint = _catalog_blueprint(engine_paths, "proj-task", [{"id": "s1", "run": "echo one"}])
        stub_drive_run(
            lambda paths, sessions, session_id: RunOutcome(
                status=SessionStatus.COMPLETED, worktree_path=paths.root_dir, worktree_id="dovo_1", worktree_kept=keep
            )
        )

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="proj-1", use_worktree=True, keep=keep)
        )

        row = sessions_repo.get("proj-1")
        assert row is not None
        payload = SessionJsonPayload.model_validate_json(
            (get_session_dir(engine_paths, "proj-1") / "session.json").read_text(encoding="utf-8")
        )
        assert payload.revision == row.execution_state_revision
        assert payload.results == outcome.step_results
        assert payload.lifecycle.status == row.status == SessionStatus.COMPLETED
        assert payload.lifecycle.error_message == row.error_message
        assert payload.lifecycle.completed_at == row.completed_at
        assert row.completed_at is not None
        assert payload.lifecycle.worktree_id == row.worktree_id == "dovo_1"
        assert payload.lifecycle.worktree_kept is row.worktree_kept is keep


class EngineFinalizeFallbackTests:
    def test_finalize_falls_back_to_row_update_when_state_is_corrupt(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        blueprint = _catalog_blueprint(engine_paths, "corrupt-task", [{"id": "s1", "run": "echo one"}])

        def corrupting_drive_run(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
            sessions.save_execution_state(session_id, "not json", expected_revision=0, next_revision=0)
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=paths.root_dir)

        stub_drive_run(corrupting_drive_run)

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="corrupt-1", use_worktree=False)
        )

        row = sessions_repo.get("corrupt-1")
        assert row is not None
        assert outcome.warnings == ["Execution state for session 'corrupt-1' is corrupt."]
        assert row.status == SessionStatus.COMPLETED
