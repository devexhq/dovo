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
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.engine import Engine, EngineResumeError, EngineResumeStatus, RunRequest
from worktree.core.engine.models import DefinitionRef, DefinitionsManifest, SessionRunPayload
from worktree.core.engine.writer import get_session_dir, load_session_run, write_session_run_json
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
        """[tier-1/unit] Engine.run: RunContext.session_id captured via monkeypatched run_steps equals the generated blueprint_<hex> sid also used for run.json."""
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

    def test_run_writes_snapshot_files_and_populates_run_json_definitions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: a catalog-backed blueprint with one uses: step produces session_dir/definitions/<key>.yml, session_dir/definitions/steps/<step_key>.yml, and run.json's definitions manifest referencing both."""
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
        payload = load_session_run(paths, "snap-1")
        assert payload is not None
        assert payload.definitions is not None
        assert payload.definitions.blueprint.ref == "repo:blueprint:snap-task"
        assert [ref.ref for ref in payload.definitions.steps] == ["repo:step:lint-check"]

    def test_run_blueprint_not_catalog_backed_leaves_definitions_none_and_still_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] Engine.run: an in-memory-only Blueprint (BlueprintBuilder, no catalog record) completes the run with RunOutcome.warnings naming the snapshot failure and run.json's definitions left None."""
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

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.warnings == ["Failed to snapshot run definitions: blueprint 'lint' not found in catalog."]
        payload = load_session_run(paths, "snap-2")
        assert payload is not None
        assert payload.definitions is None


class EngineResumePreservesDefinitionsTests:
    """[tier-1/unit] Engine.resume: run.json rewritten after resume carries the same definitions manifest captured by the original Engine.run, not None."""

    def test_resume_rewritten_run_json_keeps_original_definitions_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task_defs", checkpoint)
        manifest = DefinitionsManifest(
            blueprint=DefinitionRef(ref="repo:blueprint:lint", sha="abc123", resolved_at="2026-09-25T19:04:00+00:00"),
            steps=[],
        )
        write_session_run_json(
            get_session_dir(paths, "task_defs"),
            SessionRunPayload(
                session_id="task_defs",
                name="lint",
                status="paused",
                started_at="2026-09-25T19:00:00+00:00",
                definitions=manifest,
            ),
        )
        monkeypatch.setattr(
            "worktree.core.engine.engine.run_steps",
            lambda _context: RunOutcome(status=RunStatus.COMPLETED, sandbox_path=workspace),
        )

        Engine(paths, db=runs_repo, catalog=Catalog(paths)).resume("task_defs", blueprint=blueprint)

        payload = load_session_run(paths, "task_defs")
        assert payload is not None
        assert payload.definitions == manifest
