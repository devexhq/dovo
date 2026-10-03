"""Contract tests for Engine.run()/resume(): start persistence, dispatch through drive_run, and row finalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from tests.harness.runs import seed_paused_run
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.catalog import Catalog
from worktree.core.catalog.blueprint import Blueprint
from worktree.core.catalog.definitions import LoopStepBlock, StepDefinition
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.engine import Engine, EngineResumeError, EngineResumeStatus, RunRequest, RunStateStore
from worktree.engine.executors.models import StepResult
from worktree.engine.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunObserver,
    RunOutcome,
)
from worktree.engine.state_models import RunJsonPayload
from worktree.engine.writer import get_session_dir


class _ContinuePrompter(FailurePrompter):
    """FailurePrompter answering CONTINUE to every step failure."""

    def prompt_step_failure(
        self, *, step: StepDefinition, result: StepResult, diagnostic: str
    ) -> FailurePromptDecision:
        return FailurePromptDecision.CONTINUE

    def prompt_loop_max_iterations(
        self, *, loop: LoopStepBlock, iteration: int, diagnostic: str, grant_count: int = 3
    ) -> LoopPromptDecision:
        raise AssertionError("prompt_loop_max_iterations should not be called")


def _engine(paths: WorkspacePaths, runs: RunsRepository) -> Engine:
    return Engine(paths, db=runs, catalog=Catalog(paths))


def _catalog_blueprint(paths: WorkspacePaths, key: str, steps: list[dict[str, object]]) -> Blueprint:
    write_runnable_blueprint(paths.root_dir, key=key, steps=steps)
    return Blueprint.load(key, catalog=Catalog(paths))


def _patch_drive_run(monkeypatch: pytest.MonkeyPatch, paths: WorkspacePaths, outcome: RunOutcome | None = None) -> None:
    """Replace drive_run so Engine persistence is observed without executing steps."""
    result = outcome or RunOutcome(status=RunStatus.COMPLETED, sandbox_path=paths.root_dir)

    def fake_drive_run(
        paths: WorkspacePaths,
        runs: RunsRepository,
        session_id: str,
        *,
        observer: RunObserver | None,
        prompter: FailurePrompter | None,
        no_tty: bool,
    ) -> RunOutcome:
        return result

    monkeypatch.setattr("worktree.engine.engine.drive_run", fake_drive_run)


class EngineRunStartFailureTests:
    """[tier-1/unit] Engine.run: run-start persistence failures fail the run before any step executes."""

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
        runs_repo: RunsRepository,
        monkeypatch: pytest.MonkeyPatch,
        fault: str,
        expected_error: str,
    ) -> None:
        """[tier-1/unit] Engine.run: a snapshot, row-insert, or state-initialize fault returns status FAILED with errors[0] naming the fault, a step marker file absent, and (for state-initialize) the inserted row marked FAILED."""
        if fault == "snapshot":
            blueprint = Blueprint(
                BlueprintBuilder("uncataloged")
                .with_use_sandbox(False)
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
            monkeypatch.setattr(RunsRepository, "create", raise_db_down)
        if fault == "initialize":
            monkeypatch.setattr(RunStateStore, "initialize", raise_boom)

        outcome = _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="start-1", use_sandbox=False))

        assert outcome.status == RunStatus.FAILED
        assert outcome.errors == [expected_error]
        assert not (engine_paths.root_dir / "marker").exists()
        row = runs_repo.get("start-1")
        assert (row.status if row is not None else None) == (RunStatus.FAILED if fault == "initialize" else None)


class EngineDispatchTests:
    """[tier-1/integration] Engine.run and Engine.resume: one dispatch path through drive_run and RunCoordinator."""

    def test_run_and_resume_both_complete_through_coordinator_and_finalize_row(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] Engine.run and Engine.resume: a fresh two-step run and a resumed paused run both return COMPLETED, and each row ends status COMPLETED with execution_state_revision > 0 and the original manifest."""
        engine = _engine(engine_paths, runs_repo)
        fresh = engine.run(
            _catalog_blueprint(engine_paths, "fresh", [{"id": "a", "run": "true"}, {"id": "b", "run": "true"}]),
            RunRequest(session_id="fresh", use_sandbox=False),
        )
        seed_paused_run(
            engine_paths,
            runs_repo,
            session_id="paused",
            steps=[{"id": "a", "run": "true"}, {"id": "b", "run": "exit 1", "on_failure": "prompt_user"}],
            paused_step_id="b",
        )
        manifest = RunStateStore(runs_repo, engine_paths, "paused").load().state
        assert manifest is not None

        resumed = engine.resume("paused", failure_prompter=_ContinuePrompter())

        assert (fresh.status, resumed.status) == (RunStatus.COMPLETED, RunStatus.COMPLETED)
        assert [result.step_id for result in fresh.step_results] == ["a", "b"]
        assert [(result.step_id, result.status) for result in resumed.step_results] == [
            ("a", "completed"),
            ("b", "ignored"),
        ]
        for session_id in ("fresh", "paused"):
            row = runs_repo.get(session_id)
            assert row is not None
            assert row.status == RunStatus.COMPLETED
            assert (row.execution_state_revision or 0) > 0
        final = RunStateStore(runs_repo, engine_paths, "paused").load().state
        assert final is not None
        assert final.manifest == manifest.manifest

    @pytest.mark.parametrize(
        "fault", [pytest.param("not-found", id="not-found"), pytest.param("wrong-status", id="wrong-status")]
    )
    def test_resume_of_invalid_run_raises_before_any_step_executes(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, fault: str
    ) -> None:
        """[tier-1/integration] Engine.resume: an unknown session or a non-paused row raises EngineResumeError (NOT_FOUND / WRONG_STATUS) and the step marker file is never written."""
        if fault == "wrong-status":
            seed_paused_run(
                engine_paths,
                runs_repo,
                session_id="invalid",
                steps=[{"id": "a", "run": "touch marker", "on_failure": "prompt_user"}],
                paused_step_id="a",
            )
            runs_repo.update_status("invalid", RunStatus.COMPLETED)

        with pytest.raises(EngineResumeError) as exc_info:
            _engine(engine_paths, runs_repo).resume("invalid")

        expected = EngineResumeStatus.NOT_FOUND if fault == "not-found" else EngineResumeStatus.WRONG_STATUS
        assert exc_info.value.status is expected
        assert not (engine_paths.root_dir / "marker").exists()

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
        runs_repo: RunsRepository,
        monkeypatch: pytest.MonkeyPatch,
        entry: str,
        expected_session_id: str | None,
    ) -> None:
        """[tier-1/integration] Engine.run and Engine.resume: drive_run receives the explicit, generated, or resumed session id, and the outcome carries the same id."""
        seen: list[str] = []

        def recording_drive_run(
            paths: WorkspacePaths,
            runs: RunsRepository,
            session_id: str,
            *,
            observer: RunObserver | None,
            prompter: FailurePrompter | None,
            no_tty: bool,
        ) -> RunOutcome:
            seen.append(session_id)
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=paths.root_dir)

        monkeypatch.setattr("worktree.engine.engine.drive_run", recording_drive_run)
        engine = _engine(engine_paths, runs_repo)
        if entry == "resume":
            seed_paused_run(
                engine_paths,
                runs_repo,
                session_id="resume-1",
                steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
                paused_step_id="a",
            )
            outcome = engine.resume("resume-1")
        else:
            blueprint = _catalog_blueprint(engine_paths, "ids", [{"id": "a", "run": "true"}])
            outcome = engine.run(blueprint, RunRequest(session_id=expected_session_id, use_sandbox=False))

        assert outcome.session_id == seen[0]
        assert expected_session_id is None or seen == [expected_session_id]
        assert expected_session_id is not None or seen[0].startswith("blueprint_")


class EngineResumeFinalizationTests:
    """[tier-1/integration] Engine.resume: row status flips and finalization record persistence failures as warnings."""

    @staticmethod
    def _seed(paths: WorkspacePaths, runs: RunsRepository, session_id: str) -> None:
        seed_paused_run(
            paths,
            runs,
            session_id=session_id,
            steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
            paused_step_id="a",
        )

    def test_resume_mark_running_failure_appends_warning_but_still_runs(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] Engine.resume: a failing paused-to-running status update appends one warning, still dispatches, and the row is finalized COMPLETED."""
        self._seed(engine_paths, runs_repo, "mark-running")
        _patch_drive_run(monkeypatch, engine_paths)

        def locked(*args: object, **kwargs: object) -> None:
            raise RuntimeError("locked")

        monkeypatch.setattr(RunsRepository, "update_status", locked)

        outcome = _engine(engine_paths, runs_repo).resume("mark-running")

        assert outcome.warnings == ["Failed to update run status in database: locked"]
        row = runs_repo.get("mark-running")
        assert row is not None
        assert row.status == RunStatus.COMPLETED

    def test_resume_finalize_failure_appends_warning_and_leaves_row_running(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] Engine.resume: a failing final state save appends a warning and leaves the row running with no completed_at."""
        self._seed(engine_paths, runs_repo, "finalize")
        _patch_drive_run(monkeypatch, engine_paths)

        def locked(*args: object, **kwargs: object) -> None:
            raise RuntimeError("locked")

        monkeypatch.setattr(RunsRepository, "save_execution_state", locked)

        outcome = _engine(engine_paths, runs_repo).resume("finalize")

        assert outcome.warnings == ["Failed to update run status in database: locked"]
        row = runs_repo.get("finalize")
        assert row is not None
        assert (row.status, row.completed_at) == (RunStatus.RUNNING, None)


class EngineRunSnapshotsDefinitionsTests:
    """[tier-1/unit] Engine.run: definitions snapshotting on run start."""

    def test_run_writes_snapshot_files_and_records_manifest_in_state(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a catalog-backed blueprint with one uses: step produces session_dir/definitions/<key>.yml, session_dir/definitions/steps/<step_key>.yml, and an execution state whose manifest references both."""
        write_runnable_step(
            engine_paths.root_dir, key="lint-check", definition={"id": "lint-check", "run": "echo lint"}
        )
        blueprint = _catalog_blueprint(engine_paths, "snap-task", [{"id": "s1", "uses": "lint-check"}])
        _patch_drive_run(monkeypatch, engine_paths)

        outcome = _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="snap-1", use_sandbox=False))

        assert outcome.status == RunStatus.COMPLETED
        session_dir = get_session_dir(engine_paths, "snap-1")
        assert (session_dir / "definitions" / "snap-task.yml").is_file()
        assert (session_dir / "definitions" / "steps" / "lint-check.yml").is_file()
        loaded = RunStateStore(runs_repo, engine_paths, "snap-1").load()
        assert loaded.state is not None
        assert loaded.state.manifest.blueprint.ref == "repo:blueprint:snap-task"
        assert [ref.ref for ref in loaded.state.manifest.steps] == ["repo:step:lint-check"]

    def test_run_persists_initial_state_and_projection_before_first_step(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: when drive_run is entered, the row is RUNNING with execution_state_revision 0, RunStateStore.load() returns a tree whose nodes are the blueprint's steps in order, and run.json parses to a RunJsonPayload whose nodes equal that tree's nodes."""
        blueprint = _catalog_blueprint(
            engine_paths, "init-task", [{"id": "s1", "run": "echo one"}, {"id": "s2", "run": "echo two"}]
        )
        observed: dict[str, object] = {}

        def observing_drive_run(
            paths: WorkspacePaths,
            runs: RunsRepository,
            session_id: str,
            *,
            observer: RunObserver | None,
            prompter: FailurePrompter | None,
            no_tty: bool,
        ) -> RunOutcome:
            row = runs.get(session_id)
            assert row is not None
            loaded = RunStateStore(runs, paths, session_id).load()
            assert loaded.state is not None
            observed["status"] = row.status
            observed["revision"] = row.execution_state_revision
            observed["nodes"] = [node.id for node in loaded.state.nodes]
            observed["projection"] = RunJsonPayload.model_validate_json(
                (paths.session_dir(session_id) / "run.json").read_text(encoding="utf-8")
            ).nodes
            observed["state"] = loaded.state.nodes
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=paths.root_dir)

        monkeypatch.setattr("worktree.engine.engine.drive_run", observing_drive_run)

        _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="init-1", use_sandbox=False))

        assert observed["status"] == RunStatus.RUNNING
        assert observed["revision"] == 0
        assert observed["nodes"] == ["s1", "s2"]
        assert observed["projection"] == observed["state"]

    def test_run_finalizes_row_with_revision_one_and_completed_status(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: after a COMPLETED outcome the row is COMPLETED with completed_at set and execution_state_revision 1."""
        blueprint = _catalog_blueprint(engine_paths, "final-task", [{"id": "s1", "run": "echo one"}])
        _patch_drive_run(monkeypatch, engine_paths)

        _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="final-1", use_sandbox=False))

        row = runs_repo.get("final-1")
        assert row is not None
        assert row.status == RunStatus.COMPLETED
        assert row.completed_at is not None
        assert row.execution_state_revision == 1


class EngineResumePreservesDefinitionsTests:
    """[tier-1/unit] Engine.resume: the execution state keeps the manifest captured by the original Engine.run."""

    def test_resume_finalization_keeps_original_manifest_and_bumps_revision(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.resume: finalizing a resumed run keeps the seeded manifest and advances the state revision by one."""
        seed_paused_run(
            engine_paths,
            runs_repo,
            session_id="task_defs",
            steps=[
                {"id": "setup", "run": "echo setup"},
                {"id": "publish", "run": "exit 1", "on_failure": "prompt_user"},
            ],
            paused_step_id="publish",
        )
        before = RunStateStore(runs_repo, engine_paths, "task_defs").load().state
        assert before is not None
        _patch_drive_run(monkeypatch, engine_paths)

        _engine(engine_paths, runs_repo).resume("task_defs")

        after = RunStateStore(runs_repo, engine_paths, "task_defs").load().state
        assert after is not None
        assert after.manifest == before.manifest
        assert after.revision == before.revision + 1


class EngineRunConfigPersistenceTests:
    """[tier-1/unit] Engine.run: resolved run configuration lands on the run row."""

    def test_run_persists_resolved_configuration_on_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] Engine.run: RunRequest(use_sandbox=False, keep=True, agent='claude', auto_apply=True) with resolved inputs {'env': 'prod'} leaves a row with use_sandbox False, keep True, agent 'claude', inputs_json '{\"env\": \"prod\"}', auto_apply True, and commit_sha equal to git rev-parse HEAD."""
        workspace = WorkspaceBuilder(tmp_path / "git-workspace").with_git().with_database().build()
        paths = resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None))
        runs = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        write_runnable_blueprint(workspace, key="cfg", steps=[{"id": "s1", "run": "echo hi"}])
        blueprint = Blueprint.load("cfg", catalog=Catalog(paths))
        _patch_drive_run(monkeypatch, paths)

        _engine(paths, runs).run(
            blueprint,
            RunRequest(session_id="cfg-1", use_sandbox=False, keep=True, agent="claude", auto_apply=True),
        )

        row = runs.get("cfg-1")
        assert row is not None
        assert row.use_sandbox is False
        assert row.keep is True
        assert row.agent == "claude"
        assert row.auto_apply is True
        assert row.commit_sha == GitRunner.rev_parse(workspace)

    def test_run_persists_resolved_inputs_json_on_row(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: resolved inputs {'env': 'prod'} leave inputs_json '{\"env\": \"prod\"}' on the row."""
        blueprint = Blueprint(
            BlueprintBuilder("inputs")
            .with_use_sandbox(False)
            .with_input("env", required=True)
            .with_step(StepBuilder.command("echo hi").with_id("s1").build())
            .build()
        )
        write_runnable_blueprint(engine_paths.root_dir, key="inputs", steps=[{"id": "s1", "run": "echo hi"}])
        _patch_drive_run(monkeypatch, engine_paths)

        _engine(engine_paths, runs_repo).run(
            blueprint, RunRequest(session_id="inputs-1", inputs={"env": "prod"}, use_sandbox=False)
        )

        row = runs_repo.get("inputs-1")
        assert row is not None
        assert row.inputs_json == '{"env": "prod"}'

    def test_run_catalog_blueprint_records_manifest_tier_on_row(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a catalog-backed blueprint leaves blueprint_tier equal to the manifest blueprint ref's tier."""
        blueprint = _catalog_blueprint(engine_paths, "tier-task", [{"id": "s1", "run": "echo one"}])
        _patch_drive_run(monkeypatch, engine_paths)

        _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="tier-1", use_sandbox=False))

        row = runs_repo.get("tier-1")
        assert row is not None
        assert row.blueprint_tier == "repo"

    def test_run_outside_git_repository_stores_null_commit_sha(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a workspace that is not a git repository completes with commit_sha None on the row."""
        blueprint = _catalog_blueprint(engine_paths, "nogit-task", [{"id": "s1", "run": "echo one"}])
        _patch_drive_run(monkeypatch, engine_paths)

        outcome = _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="nogit-1", use_sandbox=False))

        row = runs_repo.get("nogit-1")
        assert row is not None
        assert outcome.status == RunStatus.COMPLETED
        assert row.commit_sha is None

    @pytest.mark.parametrize(
        ("outcome_status", "outcome_sandbox_id"),
        [
            pytest.param(RunStatus.COMPLETED, "sbx-1", id="completed-with-sandbox"),
            pytest.param(RunStatus.PAUSED, "sbx-2", id="paused-with-sandbox"),
            pytest.param(RunStatus.COMPLETED, None, id="completed-without-sandbox"),
        ],
    )
    def test_run_records_outcome_sandbox_id_on_row(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        monkeypatch: pytest.MonkeyPatch,
        outcome_status: RunStatus,
        outcome_sandbox_id: str | None,
    ) -> None:
        """[tier-1/unit] Engine.run: row.sandbox_id equals the RunOutcome.sandbox_id drive_run reported, for completed and paused runs, and stays None when no sandbox was used."""
        blueprint = _catalog_blueprint(engine_paths, "sbx-task", [{"id": "s1", "run": "echo one"}])
        _patch_drive_run(
            monkeypatch,
            engine_paths,
            RunOutcome(status=outcome_status, sandbox_path=engine_paths.root_dir, sandbox_id=outcome_sandbox_id),
        )

        _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="sbx-run", use_sandbox=True, keep=True))

        row = runs_repo.get("sbx-run")
        assert row is not None
        assert row.status == outcome_status
        assert row.sandbox_id == outcome_sandbox_id


class EngineRunProjectionTests:
    """[tier-1/integration] Engine.run: the terminal row and run.json agree."""

    @pytest.mark.parametrize("keep", [False, True])
    def test_run_terminal_row_and_run_json_agree(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch, keep: bool
    ) -> None:
        """[tier-1/integration] Engine.run: a completed sandboxed run leaves run.json.revision == row.execution_state_revision, run.json.results == outcome.step_results, and run.json.lifecycle equal to the row's status/error_message/completed_at/sandbox_id/sandbox_kept, with sandbox_kept == keep."""
        blueprint = _catalog_blueprint(engine_paths, "proj-task", [{"id": "s1", "run": "echo one"}])
        _patch_drive_run(
            monkeypatch,
            engine_paths,
            RunOutcome(
                status=RunStatus.COMPLETED, sandbox_path=engine_paths.root_dir, sandbox_id="sbx-1", sandbox_kept=keep
            ),
        )

        outcome = _engine(engine_paths, runs_repo).run(
            blueprint, RunRequest(session_id="proj-1", use_sandbox=True, keep=keep)
        )

        row = runs_repo.get("proj-1")
        assert row is not None
        payload = RunJsonPayload.model_validate_json(
            (get_session_dir(engine_paths, "proj-1") / "run.json").read_text(encoding="utf-8")
        )
        assert payload.revision == row.execution_state_revision
        assert payload.results == outcome.step_results
        assert payload.lifecycle.status == row.status == RunStatus.COMPLETED
        assert payload.lifecycle.error_message == row.error_message
        assert payload.lifecycle.completed_at == row.completed_at
        assert row.completed_at is not None
        assert payload.lifecycle.sandbox_id == row.sandbox_id == "sbx-1"
        assert payload.lifecycle.sandbox_kept is row.sandbox_kept is keep


class EngineFinalizeFallbackTests:
    """[tier-1/unit] Engine.run: finalization falls back to a plain status update when the state cannot be reloaded."""

    def test_finalize_falls_back_to_row_update_when_state_is_corrupt(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a state document corrupted during the run leaves the row COMPLETED via update_status and one warning naming the corrupt state."""
        blueprint = _catalog_blueprint(engine_paths, "corrupt-task", [{"id": "s1", "run": "echo one"}])

        def corrupting_drive_run(
            paths: WorkspacePaths,
            runs: RunsRepository,
            session_id: str,
            *,
            observer: RunObserver | None,
            prompter: FailurePrompter | None,
            no_tty: bool,
        ) -> RunOutcome:
            runs.save_execution_state(session_id, "not json", expected_revision=0, next_revision=0)
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=paths.root_dir)

        monkeypatch.setattr("worktree.engine.engine.drive_run", corrupting_drive_run)

        outcome = _engine(engine_paths, runs_repo).run(blueprint, RunRequest(session_id="corrupt-1", use_sandbox=False))

        row = runs_repo.get("corrupt-1")
        assert row is not None
        assert outcome.warnings == ["Execution state for run 'corrupt-1' is corrupt."]
        assert row.status == RunStatus.COMPLETED
