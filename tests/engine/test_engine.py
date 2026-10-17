"""Contract tests for Engine.run()/resume(): start persistence, dispatch through drive_run, and row finalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.engine import Engine, RunRequest, SessionStateStore
from dovo.engine.models import FailurePromptDecision, RunOutcome
from dovo.engine.state_models import SessionJsonPayload
from dovo.engine.writer import get_session_dir
from tests.engine.conftest import StubDriveRun
from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from tests.harness.sessions import ScriptedFailurePrompter, seed_paused_session
from tests.harness.workspace_paths import initialized_workspace_paths


def _engine(paths: WorkspacePaths, sessions: SessionsRepository) -> Engine:
    return Engine(paths, db=sessions, catalog=Catalog(paths))


def _catalog_blueprint(paths: WorkspacePaths, key: str, steps: list[dict[str, object]]) -> Blueprint:
    write_runnable_blueprint(paths.root_dir, key=key, steps=steps)
    return Blueprint.load(key, catalog=Catalog(paths))


class EngineRunStartFailureTests:
    @pytest.mark.parametrize(
        ("fault", "expected_error"),
        [
            pytest.param(
                "snapshot",
                "Failed to snapshot run definitions: blueprint 'uncataloged' not found in catalog.",
                id="snapshot",
            ),
            pytest.param("insert", "Failed to record run start in database: db down", id="row-insert"),
            pytest.param("initialize", "Failed to initialize run state: boom", id="state-initialize"),
        ],
    )
    def test_run_start_failure_returns_failed_outcome_without_executing_steps(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        fault: str,
        expected_error: str,
    ) -> None:
        if fault == "snapshot":
            blueprint = Blueprint(
                BlueprintBuilder("uncataloged")
                .with_use_worktree(False)
                .with_step(StepBuilder.command("touch marker").with_id("a").build())
                .build()
            )
        else:
            blueprint = _catalog_blueprint(engine_paths, "start-fault", [{"id": "a", "run": "touch marker"}])

        def raise_db_down(*args: object, **kwargs: object) -> None:
            raise RuntimeError("db down")

        def raise_boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("boom")

        if fault == "insert":
            monkeypatch.setattr(SessionsRepository, "create", raise_db_down)
        if fault == "initialize":
            monkeypatch.setattr(SessionStateStore, "initialize", raise_boom)

        outcome = _engine(engine_paths, sessions_repo).run(
            blueprint, RunRequest(session_id="start-1", use_worktree=False)
        )

        assert outcome.status == SessionStatus.FAILED
        assert outcome.errors == [expected_error]
        assert not (engine_paths.root_dir / "marker").exists()
        row = sessions_repo.get("start-1")
        assert (row.status if row is not None else None) == (SessionStatus.FAILED if fault == "initialize" else None)


class EngineDispatchTests:
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
        ("entry", "expected_session_id"),
        [
            pytest.param("run-explicit", "explicit-1", id="run-explicit-id"),
            pytest.param("run-generated", None, id="run-generated-id"),
            pytest.param("resume", "resume-1", id="resume-id"),
        ],
    )
    def test_run_and_resume_pass_their_session_id_to_drive_run(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        entry: str,
        expected_session_id: str | None,
    ) -> None:
        seen: list[str] = []

        def recording_drive_run(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
            seen.append(session_id)
            return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=paths.root_dir)

        stub_drive_run(recording_drive_run)
        engine = _engine(engine_paths, sessions_repo)
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
            outcome = engine.run(blueprint, RunRequest(session_id=expected_session_id, use_worktree=False))

        assert outcome.session_id == seen[0]
        assert expected_session_id is None or seen == [expected_session_id]
        assert expected_session_id is not None or seen[0].startswith("blueprint_")


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

    def test_resume_mark_running_failure_appends_warning_but_still_runs(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        stub_drive_run: StubDriveRun,
    ) -> None:
        self._seed(engine_paths, sessions_repo, "mark-running")
        stub_drive_run(None)

        def locked(*args: object, **kwargs: object) -> None:
            raise RuntimeError("locked")

        monkeypatch.setattr(SessionsRepository, "update_status", locked)

        outcome = _engine(engine_paths, sessions_repo).resume("mark-running")

        assert outcome.warnings == ["Failed to update run status in database: locked"]
        row = sessions_repo.get("mark-running")
        assert row is not None
        assert row.status == SessionStatus.COMPLETED

    def test_resume_finalize_failure_appends_warning_and_leaves_row_running(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        stub_drive_run: StubDriveRun,
    ) -> None:
        self._seed(engine_paths, sessions_repo, "finalize")
        stub_drive_run(None)

        def locked(*args: object, **kwargs: object) -> None:
            raise RuntimeError("locked")

        monkeypatch.setattr(SessionsRepository, "save_execution_state", locked)

        outcome = _engine(engine_paths, sessions_repo).resume("finalize")

        assert outcome.warnings == ["Failed to update run status in database: locked"]
        row = sessions_repo.get("finalize")
        assert row is not None
        assert (row.status, row.completed_at) == (SessionStatus.RUNNING, None)


class EngineRunSnapshotsDefinitionsTests:
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
        ) == (False, keep, discard, "claude", True, '{"env": "prod"}', "repo", GitRunner.rev_parse(workspace))

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
