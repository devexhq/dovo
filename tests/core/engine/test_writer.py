"""Tests for run-definitions snapshotting."""

from __future__ import annotations

from pathlib import Path

from tests.harness.builders import BlueprintBuilder, StepBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from worktree.common.filesystem import Filesystem
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.db import RunStatus
from worktree.core.engine.models import DefinitionRef, DefinitionsManifest
from worktree.core.engine.state_models import RunJsonPayload, RunLifecycle
from worktree.core.engine.writer import (
    load_blueprint_from_snapshot,
    snapshot_definitions,
    write_session_run_projection,
)
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.step import StepDefinition, StepType


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class SnapshotDefinitionsTests:
    """Contract tests for snapshot_definitions writing session-scoped run-definition YAML."""

    def test_snapshot_definitions_writes_blueprint_and_direct_step_and_returns_manifest(self, tmp_path: Path) -> None:
        """snapshot_definitions: single uses: step writes both YAML files under session_dir/definitions/ and returns a two-ref manifest with matching Filesystem.compute_checksum shas."""
        workspace = tmp_path / "workspace"
        write_runnable_step(workspace, key="lint-check", definition={"id": "lint-check", "run": "echo lint"})
        write_runnable_blueprint(workspace, key="run-def-task", steps=[{"id": "s1", "uses": "lint-check"}])
        catalog = Catalog(_paths_for(workspace))
        blueprint = Blueprint.load("run-def-task", catalog=catalog)
        session_dir = tmp_path / "session"
        warnings: list[str] = []

        manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)

        assert warnings == []
        assert manifest is not None
        blueprint_file = session_dir / "definitions" / "run-def-task.yml"
        step_file = session_dir / "definitions" / "steps" / "lint-check.yml"
        assert blueprint_file.is_file()
        assert step_file.is_file()
        assert manifest.blueprint.ref == "repo:blueprint:run-def-task"
        assert manifest.blueprint.sha == Filesystem.compute_checksum(blueprint_file.read_text(encoding="utf-8"))
        assert len(manifest.steps) == 1
        assert manifest.steps[0].ref == "repo:step:lint-check"
        assert manifest.steps[0].sha == Filesystem.compute_checksum(step_file.read_text(encoding="utf-8"))

    def test_snapshot_definitions_zero_uses_steps_creates_no_steps_directory(self, tmp_path: Path) -> None:
        """snapshot_definitions: a blueprint with no uses: steps returns definitions.steps == [] and never creates session_dir/definitions/steps/."""
        workspace = tmp_path / "workspace"
        write_runnable_blueprint(workspace, key="no-uses-task", steps=[{"id": "s1", "run": "echo hi"}])
        catalog = Catalog(_paths_for(workspace))
        blueprint = Blueprint.load("no-uses-task", catalog=catalog)
        session_dir = tmp_path / "session"
        warnings: list[str] = []

        manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)

        assert warnings == []
        assert manifest is not None
        assert manifest.steps == []
        assert (session_dir / "definitions" / "no-uses-task.yml").is_file()
        assert not (session_dir / "definitions" / "steps").exists()

    def test_snapshot_definitions_recursive_uses_chain_snapshots_every_hop(self, tmp_path: Path) -> None:
        """snapshot_definitions: a leaf-uses-mid-uses-root chain writes mid.yml and root.yml and lists both in definitions.steps."""
        workspace = tmp_path / "workspace"
        write_runnable_step(workspace, key="root", definition={"id": "root", "run": "echo root"})
        write_runnable_step(workspace, key="mid", definition={"id": "mid", "uses": "root", "name": "Mid Name"})
        write_runnable_step(workspace, key="leaf", definition={"id": "leaf", "uses": "mid", "name": "Leaf Name"})
        write_runnable_blueprint(workspace, key="chain-task", steps=[{"id": "s1", "uses": "leaf"}])
        catalog = Catalog(_paths_for(workspace))
        blueprint = Blueprint.load("chain-task", catalog=catalog)
        session_dir = tmp_path / "session"
        warnings: list[str] = []

        manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)

        assert warnings == []
        assert manifest is not None
        assert {ref.ref for ref in manifest.steps} == {"repo:step:leaf", "repo:step:mid", "repo:step:root"}
        assert (session_dir / "definitions" / "steps" / "leaf.yml").is_file()
        assert (session_dir / "definitions" / "steps" / "mid.yml").is_file()
        assert (session_dir / "definitions" / "steps" / "root.yml").is_file()

    def test_snapshot_definitions_blueprint_not_catalog_backed_returns_none_with_warning(self, tmp_path: Path) -> None:
        """snapshot_definitions: a Blueprint whose key is not indexed in the catalog returns None and appends a 'not found in catalog' warning."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        catalog = Catalog(_paths_for(workspace))
        blueprint = Blueprint(
            BlueprintBuilder("no-catalog-task")
            .with_use_sandbox(False)
            .with_step(StepBuilder.command("echo hi"))
            .build()
        )
        session_dir = tmp_path / "session"
        warnings: list[str] = []

        manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)

        assert manifest is None
        assert warnings == ["Failed to snapshot run definitions: blueprint 'no-catalog-task' not found in catalog."]
        assert not (session_dir / "definitions").exists()

    def test_snapshot_definitions_missing_uses_step_returns_none_with_warning(self, tmp_path: Path) -> None:
        """snapshot_definitions: a uses: reference with no matching catalog step returns None and appends a warning naming that step, writing no files."""
        workspace = tmp_path / "workspace"
        write_runnable_blueprint(workspace, key="missing-step-task", steps=[{"id": "s1", "uses": "ghost-step"}])
        catalog = Catalog(_paths_for(workspace))
        blueprint = Blueprint.load("missing-step-task", catalog=catalog)
        session_dir = tmp_path / "session"
        warnings: list[str] = []

        manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)

        assert manifest is None
        assert warnings == ["Failed to snapshot run definitions: catalog step 'ghost-step' not found."]
        assert not (session_dir / "definitions").exists()


class LoadBlueprintFromSnapshotTests:
    """Contract tests for load_blueprint_from_snapshot rebuilding a run's blueprint from its session files."""

    def test_uses_step_with_run_shorthand_base_resolves_to_command_step(self, tmp_path: Path) -> None:
        """load_blueprint_from_snapshot: a uses: step whose snapshotted base step is written with run: shorthand resolves to a command step carrying that command."""
        workspace = tmp_path / "workspace"
        write_runnable_step(workspace, key="lint-check", definition={"id": "lint-check", "run": "echo lint"})
        write_runnable_blueprint(workspace, key="shorthand-task", steps=[{"id": "s1", "uses": "lint-check"}])
        catalog = Catalog(_paths_for(workspace))
        session_dir = tmp_path / "session"
        manifest = snapshot_definitions(catalog, Blueprint.load("shorthand-task", catalog=catalog), session_dir, [])
        assert manifest is not None

        rebuilt = load_blueprint_from_snapshot(session_dir, manifest)

        step = rebuilt.steps[0]
        assert isinstance(step, StepDefinition)
        assert (step.id, step.type, step.command) == ("s1", StepType.COMMAND, "echo lint")


class WriteSessionRunProjectionTests:
    """Contract tests for write_session_run_projection writing run.json."""

    def test_write_session_run_projection_writes_indented_payload_json_and_returns_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] write_session_run_projection: returns <session_dir>/run.json whose text equals payload.model_dump_json(indent=2) and that parses back to an equal RunJsonPayload."""
        payload = RunJsonPayload(
            revision=2,
            manifest=DefinitionsManifest(
                blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="a", resolved_at="2026-09-28T00:00:00+00:00")
            ),
            lifecycle=RunLifecycle(status=RunStatus.RUNNING, started_at="2026-09-28T00:00:00+00:00"),
        )

        path = write_session_run_projection(tmp_path / "session", payload)

        assert path == tmp_path / "session" / "run.json"
        assert path.read_text(encoding="utf-8") == payload.model_dump_json(indent=2)
        assert RunJsonPayload.model_validate_json(path.read_text(encoding="utf-8")) == payload
