"""Contract tests for ResumableRun: classification, blueprint resolution, and handle contract."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.harness.builders import BlueprintBuilder, StepBuilder, WorkspaceBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.engine import EngineResumeError, EngineResumeStatus, ResumableRun
from worktree.core.engine.models import DefinitionsManifest, SessionRunPayload
from worktree.core.engine.writer import get_session_dir, snapshot_definitions, write_session_run_json
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.runtime import RunCheckpoint
from worktree.core.step.models import LoopStepBlock, StepDefinition


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _checkpoint(
    *,
    pending_step_id: str = "publish",
    use_sandbox: bool = False,
    sandbox_path: str | None = None,
) -> RunCheckpoint:
    return RunCheckpoint(
        next_step_index=1,
        pending_step_id=pending_step_id,
        diagnostic="Step 'publish' failed: boom",
        use_sandbox=use_sandbox,
        sandbox_path=sandbox_path,
    )


def _task_blueprint(*, name: str = "lint", loop: bool = False) -> tuple[Blueprint, list[StepDefinition]]:
    """Build a 3-step (setup/publish/later) task blueprint, optionally with a trailing loop block.

    Returns the blueprint alongside its flattened step list (loop sub-steps expanded, matching
    ResumableRun's own _collect_blueprint_steps), so callers can assert whole-object equality
    against handle.steps instead of re-deriving the expected steps by hand.
    """
    setup = StepBuilder.command("echo setup").with_id("setup").build()
    publish = StepBuilder.command("exit 1").with_id("publish").build()
    later = StepBuilder.command("echo later").with_id("later").build()
    builder = BlueprintBuilder(name).with_use_sandbox(False).with_step(setup).with_step(publish).with_step(later)
    steps = [setup, publish, later]
    if loop:
        unit = StepBuilder.command("echo hi").with_id("unit").build()
        builder.with_step(LoopStepBlock(id="retry", type="loop", until=["steps.unit.exit_code == 0"], do=[unit]))
        steps = [setup, publish, later, unit]
    return Blueprint(builder.build()), steps


def _seed_paused_run(db: RunsRepository, session_id: str, checkpoint: RunCheckpoint, *, name: str = "lint") -> None:
    db.create(session_id, blueprint_name=name, blueprint_key=name, status=RunStatus.RUNNING)
    db.save_pause(session_id, checkpoint.model_dump_json(), checkpoint.diagnostic)


def _seed_snapshotted_paused_run(
    workspace: Path,
    runs_repo: RunsRepository,
    session_id: str,
    *,
    blueprint_key: str,
    checkpoint: RunCheckpoint,
) -> DefinitionsManifest:
    """Snapshot blueprint_key's catalog blueprint into session_id's definitions/ dir and seed a matching paused row."""
    paths = _paths_for(workspace)
    catalog = Catalog(paths)
    blueprint = Blueprint.load(blueprint_key, catalog=catalog)
    session_dir = get_session_dir(paths, session_id)
    warnings: list[str] = []
    manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)
    assert manifest is not None
    write_session_run_json(
        session_dir,
        SessionRunPayload(
            session_id=session_id,
            name=blueprint.name,
            status="paused",
            started_at="2026-09-25T19:00:00+00:00",
            definitions=manifest,
        ),
    )
    runs_repo.create(session_id, blueprint_name=blueprint_key, blueprint_key=blueprint_key, status=RunStatus.RUNNING)
    runs_repo.save_pause(session_id, checkpoint.model_dump_json(), checkpoint.diagnostic)
    return manifest


class ResumableRunHandleTests:
    """Contract tests for ResumableRun.load's happy-path and not-found handle shape."""

    def test_resumable_run_load_is_resumable_with_explicit_blueprint(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, expected_steps = _task_blueprint()
        checkpoint = _checkpoint()
        _seed_paused_run(runs_repo, "task-1", checkpoint)

        handle = ResumableRun.load("task-1", blueprint, paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is True
        assert handle.status == EngineResumeStatus.OK
        assert handle.checkpoint == checkpoint
        assert handle.steps == expected_steps

    def test_resumable_run_load_not_found_when_session_missing(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)

        handle = ResumableRun.load("missing", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.NOT_FOUND
        assert str(handle) == "Session 'missing' not found."
        with pytest.raises(EngineResumeError) as exc_info:
            handle.ready()
        assert exc_info.value.status == EngineResumeStatus.NOT_FOUND


class ResumableRunBlueprintResolutionTests:
    """Contract tests for ResumableRun.load's blueprint auto-load and step-validity checks."""

    def test_resumable_run_load_omitted_blueprint_auto_loads_from_catalog_by_stored_blueprint_key(
        self, tmp_path: Path
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
        _seed_paused_run(runs_repo, "task-2", _checkpoint(), name="lint")

        handle = ResumableRun.load("task-2", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is True
        assert handle.blueprint is not None
        assert handle.blueprint.definition.name == "lint"
        assert [s.id for s in handle.blueprint.definition.steps] == ["setup", "publish", "later"]

    def test_resumable_run_load_omitted_blueprint_missing_from_catalog_is_classified_failed(
        self, tmp_path: Path
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        _seed_paused_run(runs_repo, "task-3", _checkpoint(), name="missing-task")

        handle = ResumableRun.load("task-3", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.FAILED
        assert "blueprint 'missing-task' not found" in str(handle)

    def test_resumable_run_load_pending_step_removed_from_blueprint_is_classified_corrupt_checkpoint(
        self, tmp_path: Path
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, _ = _task_blueprint()
        checkpoint = _checkpoint(pending_step_id="removed-step")
        _seed_paused_run(runs_repo, "task-4", checkpoint)

        handle = ResumableRun.load("task-4", blueprint, paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.CORRUPT_CHECKPOINT
        assert "checkpoint is missing or corrupt" in str(handle)

    def test_resumable_run_load_accepts_loop_steps_in_workflow_blueprint(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        blueprint, expected_steps = _task_blueprint(loop=True)
        checkpoint = _checkpoint(pending_step_id="unit")
        _seed_paused_run(runs_repo, "task-5", checkpoint)

        handle = ResumableRun.load("task-5", blueprint, paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is True
        assert handle.steps == expected_steps


class ResumableRunClassificationTests:
    """Contract tests for every remaining non-OK EngineResumeStatus classification branch."""

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(RunStatus.COMPLETED, id="status_completed"),
            pytest.param(RunStatus.FAILED, id="status_failed"),
            pytest.param(RunStatus.RUNNING, id="status_running"),
            pytest.param(RunStatus.CANCELLED, id="status_cancelled"),
        ],
    )
    def test_resumable_run_load_non_paused_row_is_classified_wrong_status(
        self, tmp_path: Path, status: RunStatus
    ) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        runs_repo.create("task_wrong", blueprint_name="lint", blueprint_key="lint", status=status)

        handle = ResumableRun.load("task_wrong", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.status == EngineResumeStatus.WRONG_STATUS
        assert str(handle) == f"Cannot resume session 'task_wrong': status is '{status.value}' (expected paused)."

    def test_resumable_run_load_corrupt_checkpoint_json_is_classified_corrupt_checkpoint(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        runs_repo.create("task-6", blueprint_name="lint", blueprint_key="lint", status=RunStatus.RUNNING)
        runs_repo.save_pause("task-6", "not valid json", "boom")

        handle = ResumableRun.load("task-6", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.CORRUPT_CHECKPOINT

    def test_resumable_run_load_missing_sandbox_path_is_classified_missing_sandbox(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        checkpoint = _checkpoint(use_sandbox=True, sandbox_path=str(tmp_path / "gone"))
        _seed_paused_run(runs_repo, "task-7", checkpoint)

        handle = ResumableRun.load("task-7", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.MISSING_SANDBOX


class ResumableRunSnapshotResolutionTests:
    """Contract tests for ResumableRun.load resolving a paused session's blueprint from its run.json snapshot."""

    def test_resumable_run_load_uses_snapshot_when_definitions_manifest_present_and_catalog_deleted(
        self, tmp_path: Path
    ) -> None:
        """ResumableRun.load: a session with a definitions manifest and matching snapshot files resolves is_resumable True even after the catalog blueprint file is deleted."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        write_runnable_blueprint(
            workspace,
            key="snap-resume-task",
            steps=[{"id": "s1", "run": "echo one"}, {"id": "s2", "run": "echo two"}],
        )
        checkpoint = _checkpoint(pending_step_id="s2")
        _seed_snapshotted_paused_run(
            workspace, runs_repo, "snap-1", blueprint_key="snap-resume-task", checkpoint=checkpoint
        )
        (workspace / ".worktree" / "catalog" / "blueprints" / "snap-resume-task.yml").unlink()

        handle = ResumableRun.load("snap-1", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is True
        assert handle.status == EngineResumeStatus.OK
        assert [s.id for s in handle.steps] == ["s1", "s2"]

    def test_resumable_run_load_recursive_uses_chain_resolves_fully_from_snapshot(self, tmp_path: Path) -> None:
        """ResumableRun.load: a leaf-uses-mid-uses-root snapshot resolves handle.blueprint's step to root's concrete command with no catalog present."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        write_runnable_step(
            workspace, key="root", definition={"id": "root", "type": "command", "command": "echo root-command"}
        )
        write_runnable_step(workspace, key="mid", definition={"id": "mid", "uses": "root", "name": "Mid Name"})
        write_runnable_step(workspace, key="leaf", definition={"id": "leaf", "uses": "mid", "name": "Leaf Name"})
        write_runnable_blueprint(workspace, key="chain-resume-task", steps=[{"id": "s1", "uses": "leaf"}])
        checkpoint = _checkpoint(pending_step_id="s1")
        _seed_snapshotted_paused_run(
            workspace, runs_repo, "snap-2", blueprint_key="chain-resume-task", checkpoint=checkpoint
        )
        shutil.rmtree(workspace / ".worktree" / "catalog")

        handle = ResumableRun.load("snap-2", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is True
        assert len(handle.steps) == 1
        assert handle.steps[0].id == "s1"
        assert handle.steps[0].command == "echo root-command"

    def test_resumable_run_load_missing_snapshot_file_is_classified_missing_snapshot(self, tmp_path: Path) -> None:
        """ResumableRun.load: a definitions manifest referencing a deleted snapshot file classifies status MISSING_SNAPSHOT with the missing path in the message."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        write_runnable_blueprint(workspace, key="missing-snap-task", steps=[{"id": "s1", "run": "echo one"}])
        checkpoint = _checkpoint(pending_step_id="s1")
        _seed_snapshotted_paused_run(
            workspace, runs_repo, "snap-3", blueprint_key="missing-snap-task", checkpoint=checkpoint
        )
        blueprint_snapshot = get_session_dir(paths, "snap-3") / "definitions" / "missing-snap-task.yml"
        blueprint_snapshot.unlink()

        handle = ResumableRun.load("snap-3", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.MISSING_SNAPSHOT
        assert str(blueprint_snapshot) in str(handle)

    def test_resumable_run_load_corrupt_snapshot_blueprint_is_classified_failed(self, tmp_path: Path) -> None:
        """ResumableRun.load: a hand-corrupted snapshot blueprint YAML classifies status FAILED, matching today's catalog-load failure classification."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_database().build()
        paths = _paths_for(workspace)
        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        write_runnable_blueprint(workspace, key="corrupt-snap-task", steps=[{"id": "s1", "run": "echo one"}])
        checkpoint = _checkpoint(pending_step_id="s1")
        _seed_snapshotted_paused_run(
            workspace, runs_repo, "snap-4", blueprint_key="corrupt-snap-task", checkpoint=checkpoint
        )
        blueprint_snapshot = get_session_dir(paths, "snap-4") / "definitions" / "corrupt-snap-task.yml"
        blueprint_snapshot.write_text("just a plain string, not a mapping\n", encoding="utf-8")

        handle = ResumableRun.load("snap-4", paths=paths, db=runs_repo, catalog=Catalog(paths))

        assert handle.is_resumable is False
        assert handle.status == EngineResumeStatus.FAILED
