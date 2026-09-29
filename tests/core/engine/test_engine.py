"""Contract tests for Engine.run()/resume(): context rebuild, definitions snapshotting, DB finalize, error propagation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.config.models import ConfigTier
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine import Engine, EngineResumeError, EngineResumeStatus, RunRequest, RunStateStore
from worktree.core.engine.state_models import ExecutionLeafNode, ExecutionStateTree
from worktree.core.engine.writer import get_session_dir, snapshot_definitions
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.runtime import ExecutionIdentity, RunCheckpoint, RunContext, RunOutcome
from worktree.core.step.models import LoopStepBlock, StepDefinition


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _checkpoint(
    *,
    pending_step_id: str = "publish",
    use_sandbox: bool = False,
    sandbox_path: str | None = None,
    keep: bool = False,
    agent: str | None = None,
    inputs: dict[str, str | int | bool] | None = None,
) -> RunCheckpoint:
    return RunCheckpoint(
        next_step_index=1,
        pending_step_id=pending_step_id,
        diagnostic="Step 'publish' failed: boom",
        use_sandbox=use_sandbox,
        sandbox_path=sandbox_path,
        keep=keep,
        agent=agent,
        inputs=inputs or {},
    )


def _task_blueprint(
    *, name: str = "lint", loop: bool = False
) -> tuple[Blueprint, list[StepDefinition | LoopStepBlock]]:
    """Build a 3-step (setup/publish/later) blueprint, optionally with a trailing loop block.

    Returns the blueprint alongside its flattened step list (loop sub-steps expanded, matching
    Engine.resume's own ResumableRun._collect_blueprint_steps), so callers can assert whole-object
    equality against the captured RunContext.steps instead of re-deriving expected steps by hand.
    """
    setup = StepBuilder.command("echo setup").with_id("setup").build()
    publish = StepBuilder.command("exit 1").with_id("publish").build()
    later = StepBuilder.command("echo later").with_id("later").build()
    builder = BlueprintBuilder(name).with_use_sandbox(False).with_step(setup).with_step(publish).with_step(later)
    steps: list[StepDefinition | LoopStepBlock] = [setup, publish, later]
    if loop:
        unit = StepBuilder.command("echo hi").with_id("unit").build()
        builder.with_step(LoopStepBlock(id="retry", type="loop", until=["steps.unit.exit_code == 0"], do=[unit]))
        steps = [setup, publish, later, unit]
    return Blueprint(builder.build()), steps


def _make_step_definition(step_id: str, run: str) -> StepDefinition:
    """Build the StepDefinition a bare YAML `run:` shorthand step resolves to."""
    return StepDefinition(
        id=step_id,
        uses=None,
        run=run,
        name=None,
        type=None,
        description=None,
        command=None,
        prompt=None,
        script_path=None,
        tools=[],
        env={},
        timeout_seconds=120,
        assert_=None,
        on_failure=OnFailureSpec(
            action=FailurePolicy.ABORT, max_retries=3, backoff_ms=0, on_max_retries=FailurePolicy.ABORT
        ),
    )


def _seed_paused_run(db: RunsRepository, session_id: str, checkpoint: RunCheckpoint, *, name: str = "lint") -> None:
    db.create(session_id, blueprint_name=name, blueprint_key=name, status=RunStatus.RUNNING)
    db.save_pause(session_id, checkpoint.model_dump_json(), checkpoint.diagnostic)


class EngineResumeOrchestrationTests:
    """Contract tests for Engine.resume()'s context rebuild, DB finalize, and error wrapping."""

    def test_resume_rebuilds_run_context_from_checkpoint_and_blueprint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, expected_steps = _task_blueprint()
        checkpoint = _checkpoint(keep=True, agent="copilot", inputs={"name": "demo"})
        _seed_paused_run(runs_repo, "task_resume", checkpoint)
        observer = MagicMock()
        expected = RunOutcome(status=RunStatus.COMPLETED, step_results=[], sandbox_path=workspace)
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return expected

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume(
            "task_resume",
            blueprint=blueprint,
            observer=observer,
            no_tty=True,
            failure_prompter=None,
        )

        assert outcome == expected.model_copy(update={"session_id": "task_resume"})
        context = captured["context"]
        assert context.pause_store is not None
        assert context == RunContext(
            steps=expected_steps,
            cwd=workspace.resolve(),
            use_sandbox=False,
            keep=True,
            agent="copilot",
            observer=observer,
            inputs={"name": "demo"},
            identity=ExecutionIdentity(blueprint_name="lint", blueprint_key="lint"),
            session_id="task_resume",
            no_tty=True,
            failure_prompter=None,
            pause_store=context.pause_store,
            resume_from=checkpoint,
            auto_apply=False,
            config=context.config,
            paths=paths,
        )

    def test_resume_omitted_blueprint_resolves_from_catalog_and_completes_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        blueprints_dir = workspace / ".worktree" / "catalog" / "blueprints"
        blueprints_dir.mkdir(parents=True, exist_ok=True)
        raw_yaml = (
            "steps:\n"
            "  - id: setup\n    run: echo setup\n"
            "  - id: publish\n    run: echo publish\n"
            "  - id: later\n    run: echo later\n"
        )
        (blueprints_dir / "lint.yml").write_text(raw_yaml, encoding="utf-8")
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task_catalog", checkpoint)
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("task_catalog")

        assert outcome == RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace).model_copy(
            update={"session_id": "task_catalog"}
        )
        context = captured["context"]
        assert context.pause_store is not None
        assert context == RunContext(
            steps=[
                _make_step_definition("setup", "echo setup"),
                _make_step_definition("publish", "echo publish"),
                _make_step_definition("later", "echo later"),
            ],
            cwd=workspace.resolve(),
            use_sandbox=False,
            keep=False,
            agent=None,
            observer=None,
            inputs=None,
            identity=ExecutionIdentity(blueprint_name="lint", blueprint_key="lint"),
            session_id="task_catalog",
            no_tty=False,
            failure_prompter=None,
            pause_store=context.pause_store,
            resume_from=checkpoint,
            auto_apply=False,
            config=context.config,
            paths=paths,
        )

    @pytest.mark.parametrize(
        ("blueprint_kind", "session_id"),
        [pytest.param("task", "task_done", id="task"), pytest.param("workflow", "workflow_done", id="workflow")],
    )
    def test_resume_finalizes_run_row_status_and_completed_at(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        blueprint_kind: str,
        session_id: str,
    ) -> None:
        name = "lint" if blueprint_kind == "task" else "ship"
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint(name=name)
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, session_id, checkpoint, name=name)
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume(session_id, blueprint=blueprint)

        assert outcome == RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace).model_copy(
            update={"session_id": session_id}
        )
        record = runs_repo.get(session_id)
        assert record is not None
        assert record.session_id == session_id
        assert record.blueprint_key == name
        assert record.blueprint_name == name
        assert record.status == RunStatus.COMPLETED
        assert record.checkpoint_json == checkpoint.model_dump_json()
        assert record.completed_at is not None

    def test_resume_accepts_loop_steps_in_workflow_and_finalizes_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint(name="ship", loop=True)
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "workflow_loop", checkpoint, name="ship")
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("workflow_loop", blueprint=blueprint)

        assert outcome == RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace).model_copy(
            update={"session_id": "workflow_loop"}
        )
        record = runs_repo.get("workflow_loop")
        assert record is not None
        assert record.session_id == "workflow_loop"
        assert record.blueprint_key == "ship"
        assert record.blueprint_name == "ship"
        assert record.status == RunStatus.COMPLETED
        assert record.checkpoint_json == checkpoint.model_dump_json()
        assert record.completed_at is not None

    def test_resume_mark_running_failure_appends_warning_but_still_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        _seed_paused_run(runs_repo, "task_mark", _checkpoint())
        expected = RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace, warnings=["step note"])
        monkeypatch.setattr("worktree.core.engine.engine.run_steps", lambda _context: expected)

        def _always_raise(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("locked")

        monkeypatch.setattr(RunsRepository, "update_status", _always_raise)

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("task_mark", blueprint=blueprint)

        assert outcome == expected.model_copy(
            update={
                "session_id": "task_mark",
                "warnings": [
                    "step note",
                    "Failed to update run status in database: locked",
                    "Failed to update run status in database: locked",
                ],
            }
        )

    def test_resume_finalize_status_update_failure_appends_warning_and_leaves_row_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task_final", checkpoint)
        expected = RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)
        monkeypatch.setattr("worktree.core.engine.engine.run_steps", lambda _context: expected)

        real_update_status = RunsRepository.update_status

        def _fail_non_running_finalize(
            self_repo: RunsRepository, session_id: str, status: RunStatus, **kwargs: Any
        ) -> None:
            if status != RunStatus.RUNNING:
                raise RuntimeError("locked")
            real_update_status(self_repo, session_id, status, **kwargs)

        monkeypatch.setattr(RunsRepository, "update_status", _fail_non_running_finalize)

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("task_final", blueprint=blueprint)

        assert outcome == expected.model_copy(
            update={
                "session_id": "task_final",
                "warnings": ["Failed to update run status in database: locked"],
            }
        )
        record = runs_repo.get("task_final")
        assert record is not None
        assert record.session_id == "task_final"
        assert record.blueprint_key == "lint"
        assert record.blueprint_name == "lint"
        assert record.status == RunStatus.RUNNING
        assert record.checkpoint_json == checkpoint.model_dump_json()
        assert record.completed_at is None

    @pytest.mark.parametrize("blueprint_given", [True, False], ids=["explicit_blueprint", "omitted_blueprint"])
    def test_resume_missing_session_raises_engine_resume_error_with_not_found_status(
        self, tmp_path: Path, blueprint_given: bool
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        engine = Engine(paths, db=runs_repo, catalog=Catalog(paths))

        with pytest.raises(EngineResumeError, match=r"Session 'missing' not found\.") as exc_info:
            if blueprint_given:
                engine.resume("missing", blueprint=blueprint)
            else:
                engine.resume("missing")

        assert exc_info.value.status is EngineResumeStatus.NOT_FOUND


class EngineConfigResolutionTests:
    """[tier-1/unit] Engine.run/resume: RunContext.config observes the hierarchical Repo/Global/User merge."""

    def test_run_passes_hierarchically_merged_config_into_run_context(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-1/unit] Engine.run: RunContext.config captured via monkeypatched run_steps equals the Global/User/Repo-merged WorktreeConfig for the workspace."""
        write_tier_config(ConfigTier.USER, {"agent": {"model": "user-tier-model"}})
        workspace = (
            WorkspaceBuilder(tmp_path / "workspace")
            .with_database()
            .with_config(data={"version": 1, "project": {"name": "engine-test"}, "sandbox": {"base_ref": "main"}})
            .build()
        )
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).run(blueprint)

        context = captured["context"]
        assert context.config is not None
        assert context.config.agent.model == "user-tier-model"
        assert context.config.sandbox.base_ref == "main"

    def test_resume_passes_hierarchically_merged_config_into_run_context(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-1/unit] Engine.resume: RunContext.config captured via monkeypatched run_steps equals the Global/User/Repo-merged WorktreeConfig for the workspace."""
        write_tier_config(ConfigTier.USER, {"agent": {"model": "user-tier-model"}})
        workspace = (
            WorkspaceBuilder(tmp_path / "workspace")
            .with_database()
            .with_config(data={"version": 1, "project": {"name": "engine-test"}, "sandbox": {"base_ref": "main"}})
            .build()
        )
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task_config_merge", checkpoint)
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("task_config_merge", blueprint=blueprint)

        context = captured["context"]
        assert context.config is not None
        assert context.config.agent.model == "user-tier-model"
        assert context.config.sandbox.base_ref == "main"


class EngineRunContextSessionIdTests:
    """[tier-1/unit] Engine.run/resume: RunContext.session_id observes the canonical run session id."""

    def test_run_passes_generated_session_id_into_run_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: RunContext.session_id captured via monkeypatched run_steps equals the generated blueprint_<hex> sid also used for the run row."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).run(blueprint)

        context = captured["context"]
        assert context.session_id is not None
        assert context.session_id == outcome.session_id
        assert context.session_id.startswith("blueprint_")

    def test_run_passes_explicit_request_session_id_into_run_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: RunContext.session_id captured via monkeypatched run_steps equals RunRequest.session_id when explicitly provided."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).run(blueprint, RunRequest(session_id="explicit-session"))

        assert captured["context"].session_id == "explicit-session"

    def test_resume_passes_resumed_session_id_into_run_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.resume: RunContext.session_id captured via monkeypatched run_steps equals the resumed session_id argument."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task_resume_ctx", checkpoint)
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("task_resume_ctx", blueprint=blueprint)

        assert captured["context"].session_id == "task_resume_ctx"


class EngineSingleRunContextConstructionTests:
    """[tier-2/unit] Run and resume preserve the Engine path snapshot in their contexts."""

    def test_run_and_resume_both_populate_run_context_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both orchestration entry points pass the identical Engine WorkspacePaths object to run_steps."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "paths-resume", checkpoint)
        captured: list[RunContext] = []

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured.append(context)
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)
        engine = Engine(paths, db=runs_repo, catalog=Catalog(paths))

        engine.run(blueprint, RunRequest(session_id="paths-run", use_sandbox=False))
        engine.resume("paths-resume", blueprint=blueprint)

        assert len(captured) == 2
        assert captured[0].paths is paths
        assert captured[1].paths is paths


class EngineRunSnapshotsDefinitionsTests:
    """[tier-1/unit] Engine.run: definitions snapshotting on run start."""

    def test_run_writes_snapshot_files_and_records_manifest_in_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a catalog-backed blueprint with one uses: step produces session_dir/definitions/<key>.yml, session_dir/definitions/steps/<step_key>.yml, and an execution state whose manifest references both."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        write_runnable_step(workspace, key="lint-check", definition={"id": "lint-check", "run": "echo lint"})
        write_runnable_blueprint(workspace, key="snap-task", steps=[{"id": "s1", "uses": "lint-check"}])
        catalog = Catalog(paths)
        blueprint = Blueprint.load("snap-task", catalog=catalog)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        outcome = Engine(paths, db=runs_repo, catalog=catalog).run(
            blueprint, RunRequest(session_id="snap-1", use_sandbox=False)
        )

        assert outcome.status == RunStatus.COMPLETED
        session_dir = get_session_dir(paths, "snap-1")
        assert (session_dir / "definitions" / "snap-task.yml").is_file()
        assert (session_dir / "definitions" / "steps" / "lint-check.yml").is_file()
        loaded = RunStateStore(runs_repo, paths, "snap-1").load()
        assert loaded.state is not None
        assert loaded.state.manifest.blueprint.ref == "repo:blueprint:snap-task"
        assert [ref.ref for ref in loaded.state.manifest.steps] == ["repo:step:lint-check"]

    def test_run_persists_initial_state_and_projection_before_first_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: when run_steps is entered, the row is RUNNING with execution_state_revision 0, RunStateStore.load() returns a tree whose nodes are the blueprint's steps in order, and run.json parses to that same tree."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        write_runnable_blueprint(
            workspace, key="init-task", steps=[{"id": "s1", "run": "echo one"}, {"id": "s2", "run": "echo two"}]
        )
        catalog = Catalog(paths)
        blueprint = Blueprint.load("init-task", catalog=catalog)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        observed: dict[str, object] = {}

        def fake_run_steps(_context: RunContext) -> RunOutcome:
            row = runs_repo.get("init-1")
            assert row is not None
            observed["status"] = row.status
            observed["revision"] = row.execution_state_revision
            observed["loaded"] = RunStateStore(runs_repo, paths, "init-1").load().state
            observed["projection"] = ExecutionStateTree.model_validate_json(
                (get_session_dir(paths, "init-1") / "run.json").read_text(encoding="utf-8")
            )
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        Engine(paths, db=runs_repo, catalog=catalog).run(blueprint, RunRequest(session_id="init-1", use_sandbox=False))

        loaded = observed["loaded"]
        assert isinstance(loaded, ExecutionStateTree)
        assert observed["status"] == RunStatus.RUNNING
        assert observed["revision"] == 0
        assert loaded.nodes == [ExecutionLeafNode(id="s1"), ExecutionLeafNode(id="s2")]
        assert observed["projection"] == loaded

    def test_run_finalizes_row_with_revision_one_and_completed_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a completing run leaves the row COMPLETED with completed_at set, execution_state_revision 1, and run.json at revision 1."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        write_runnable_blueprint(workspace, key="final-task", steps=[{"id": "s1", "run": "echo one"}])
        catalog = Catalog(paths)
        blueprint = Blueprint.load("final-task", catalog=catalog)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        Engine(paths, db=runs_repo, catalog=catalog).run(blueprint, RunRequest(session_id="final-1", use_sandbox=False))

        row = runs_repo.get("final-1")
        assert row is not None
        assert row.status == RunStatus.COMPLETED
        assert row.completed_at is not None
        assert row.execution_state_revision == 1
        projection = ExecutionStateTree.model_validate_json(
            (get_session_dir(paths, "final-1") / "run.json").read_text(encoding="utf-8")
        )
        assert projection.revision == 1

    def test_run_blueprint_not_catalog_backed_runs_without_state_and_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: an in-memory-only Blueprint (BlueprintBuilder, no catalog record) completes with RunOutcome.warnings naming the snapshot failure, a COMPLETED row, and execution_state_json None."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).run(
            blueprint, RunRequest(session_id="snap-2", use_sandbox=False)
        )

        row = runs_repo.get("snap-2")
        assert row is not None
        assert outcome.status == RunStatus.COMPLETED
        assert outcome.warnings == ["Failed to snapshot run definitions: blueprint 'lint' not found in catalog."]
        assert row.status == RunStatus.COMPLETED
        assert row.execution_state_json is None


class EngineResumePreservesDefinitionsTests:
    """[tier-1/unit] Engine.resume: the execution state keeps the manifest captured by the original Engine.run."""

    def test_resume_finalization_keeps_original_manifest_and_bumps_revision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        write_runnable_blueprint(
            workspace,
            key="task_defs",
            steps=[{"id": "setup", "run": "echo setup"}, {"id": "publish", "run": "exit 1"}],
        )
        catalog = Catalog(paths)
        blueprint = Blueprint.load("task_defs", catalog=catalog)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        manifest = snapshot_definitions(catalog, blueprint, get_session_dir(paths, "task_defs"), [])
        assert manifest is not None
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task_defs", checkpoint, name="task_defs")
        RunStateStore(runs_repo, paths, "task_defs").initialize(blueprint, manifest)
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        Engine(paths, db=runs_repo, catalog=catalog).resume("task_defs", blueprint=blueprint)

        loaded = RunStateStore(runs_repo, paths, "task_defs").load()
        assert loaded.state is not None
        assert loaded.state.manifest == manifest
        assert loaded.state.revision == 1


class EngineRunConfigPersistenceTests:
    """[tier-1/unit] Engine.run: resolved run configuration lands on the run row."""

    def test_run_persists_resolved_configuration_on_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] Engine.run: RunRequest(use_sandbox=False, keep=True, agent='claude', auto_apply=True) with resolved inputs {'env': 'prod'} leaves a row with use_sandbox False, keep True, agent 'claude', inputs_json '{\"env\": \"prod\"}', auto_apply True, and commit_sha equal to git rev-parse HEAD."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint = Blueprint(
            BlueprintBuilder("cfg")
            .with_use_sandbox(False)
            .with_input("env", required=True)
            .with_step(StepBuilder.command("echo hi").with_id("s1").build())
            .build()
        )
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).run(
            blueprint,
            RunRequest(
                session_id="cfg-1",
                inputs={"env": "prod"},
                use_sandbox=False,
                keep=True,
                agent="claude",
                auto_apply=True,
            ),
        )

        row = runs_repo.get("cfg-1")
        assert row is not None
        assert row.use_sandbox is False
        assert row.keep is True
        assert row.agent == "claude"
        assert row.inputs_json == '{"env": "prod"}'
        assert row.auto_apply is True
        assert row.commit_sha == GitRunner.rev_parse(workspace)

    def test_run_catalog_blueprint_records_manifest_tier_on_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a catalog-backed blueprint leaves blueprint_tier equal to the manifest blueprint ref's tier."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        write_runnable_blueprint(workspace, key="tier-task", steps=[{"id": "s1", "run": "echo one"}])
        catalog = Catalog(paths)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        Engine(paths, db=runs_repo, catalog=catalog).run(
            Blueprint.load("tier-task", catalog=catalog), RunRequest(session_id="tier-1", use_sandbox=False)
        )

        row = runs_repo.get("tier-1")
        assert row is not None
        assert row.blueprint_tier == "repo"

    def test_run_outside_git_repository_stores_null_commit_sha(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a workspace that is not a git repository completes with commit_sha None on the row."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        outcome = Engine(paths, db=runs_repo, catalog=Catalog(paths)).run(
            blueprint, RunRequest(session_id="nogit-1", use_sandbox=False)
        )

        row = runs_repo.get("nogit-1")
        assert row is not None
        assert outcome.status == RunStatus.COMPLETED
        assert row.commit_sha is None

    @pytest.mark.parametrize(
        "catalog_backed", [pytest.param(True, id="state-backed"), pytest.param(False, id="stateless")]
    )
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
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        outcome_status: RunStatus,
        outcome_sandbox_id: str | None,
        catalog_backed: bool,
    ) -> None:
        """[tier-1/unit] Engine.run: row.sandbox_id equals the RunOutcome.sandbox_id run_steps reported, for completed and paused runs with and without persisted execution state, and stays None when no sandbox was used."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        catalog = Catalog(paths)
        if catalog_backed:
            write_runnable_blueprint(workspace, key="sbx-task", steps=[{"id": "s1", "run": "echo one"}])
            blueprint = Blueprint.load("sbx-task", catalog=catalog)
        else:
            blueprint, _ = _task_blueprint()
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=outcome_status, sandbox_path=workspace, sandbox_id=outcome_sandbox_id),
        )

        Engine(paths, db=runs_repo, catalog=catalog).run(
            blueprint, RunRequest(session_id="sbx-run", use_sandbox=True, keep=True)
        )

        row = runs_repo.get("sbx-run")
        assert row is not None
        assert row.status == outcome_status
        assert row.sandbox_id == outcome_sandbox_id


class EngineStatePersistenceFailureTests:
    """[tier-1/unit] Engine.run: execution-state persistence failures surface as outcome warnings without aborting the run."""

    @pytest.mark.parametrize(
        ("fault", "expected_warning", "expected_status", "expected_revision"),
        [
            pytest.param(
                "initialize-conflict",
                "Execution state for run 'fault-1' was changed by another writer.",
                RunStatus.COMPLETED,
                0,
                id="initialize-reports-first-error",
            ),
            pytest.param(
                "initialize-raises",
                "Failed to initialize run state: boom",
                RunStatus.COMPLETED,
                None,
                id="initialize-exception-becomes-warning",
            ),
            pytest.param(
                "finalize-conflict",
                "Execution state for run 'fault-1' was changed by another writer.",
                RunStatus.RUNNING,
                0,
                id="finalize-save-conflict-leaves-row-running",
            ),
            pytest.param(
                "corrupt-state",
                "Execution state for run 'fault-1' is corrupt.",
                RunStatus.COMPLETED,
                0,
                id="finalize-falls-back-when-state-corrupt",
            ),
        ],
    )
    def test_run_state_persistence_fault_appends_warning_and_completes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fault: str,
        expected_warning: str,
        expected_status: RunStatus,
        expected_revision: int | None,
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        write_runnable_blueprint(workspace, key="fault-task", steps=[{"id": "s1", "run": "echo one"}])
        catalog = Catalog(paths)
        blueprint = Blueprint.load("fault-task", catalog=catalog)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        real_save = runs_repo.save_execution_state
        failing_call = {"initialize-conflict": 1, "finalize-conflict": 2}.get(fault)
        save_calls: list[str] = []

        def save_execution_state(*args: Any, **kwargs: Any) -> RunRecord | None:
            save_calls.append("call")
            if len(save_calls) == failing_call:
                return None
            return real_save(*args, **kwargs)

        def initialize_raises(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

        def fake_run_steps(_context: RunContext) -> RunOutcome:
            if fault == "corrupt-state":
                real_save("fault-1", "not json", expected_revision=0, next_revision=0)
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr(runs_repo, "save_execution_state", save_execution_state)
        if fault == "initialize-raises":
            monkeypatch.setattr(RunStateStore, "initialize", initialize_raises)
        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        outcome = Engine(paths, db=runs_repo, catalog=catalog).run(
            blueprint, RunRequest(session_id="fault-1", use_sandbox=False)
        )

        row = runs_repo.get("fault-1")
        assert row is not None
        assert outcome.warnings == [expected_warning]
        assert row.status == expected_status
        if expected_revision is not None:
            assert row.execution_state_revision == expected_revision


class EngineResumeAutoApplyTests:
    """[tier-1/unit] Engine.resume: auto_apply is restored from the run row."""

    @pytest.mark.parametrize("auto_apply", [pytest.param(True, id="true"), pytest.param(False, id="false")])
    def test_resume_restores_auto_apply_from_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auto_apply: bool
    ) -> None:
        """[tier-1/unit] Engine.resume: a paused row with auto_apply set yields a run_steps RunContext with the same auto_apply."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint()
        runs_repo.create("apply-1", blueprint_name="lint", blueprint_key="lint", auto_apply=auto_apply)
        runs_repo.save_pause("apply-1", checkpoint.model_dump_json(), checkpoint.diagnostic)
        captured: dict[str, RunContext] = {}

        def fake_run_steps(context: RunContext) -> RunOutcome:
            captured["context"] = context
            return RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace)

        monkeypatch.setattr("worktree.core.engine.engine.run_steps", fake_run_steps)

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("apply-1", blueprint=blueprint)

        assert captured["context"].auto_apply is auto_apply
