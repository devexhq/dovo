"""Tests for project-aware session run payload loading and run-definitions snapshotting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.harness.builders import BlueprintBuilder, StepBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from worktree.common.filesystem import Filesystem
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.diff.writer import get_session_dir
from worktree.core.engine.models import SessionRunPayload
from worktree.core.engine.writer import load_session_run, snapshot_definitions, write_session_run_json
from worktree.core.project.models import ProjectIdentity
from worktree.core.project.services.identity import save_project_identity
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class SessionRunWriterTests:
    """Integration tests for reading project-aware run metadata."""

    def test_load_session_run_with_project_identity_reads_global_run_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A global run payload is read while a conflicting local payload is ignored."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        global_payload = SessionRunPayload(
            session_id="session-626",
            name="Global session",
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
        )
        local_payload = SessionRunPayload(
            session_id="session-626",
            name="Local session",
            status="failed",
            started_at="2026-01-01T00:00:00+00:00",
        )
        monkeypatch.setenv("WORKTREE_HOME", str(global_root))
        save_project_identity(repository / ".worktree" / "project.json", identity)
        repo_paths = _paths_for(repository)
        write_session_run_json(get_session_dir(repo_paths, "session-626"), global_payload)
        local_session_dir = repository / ".worktree" / "sessions" / "session-626"
        local_session_dir.mkdir(parents=True)
        write_session_run_json(local_session_dir, local_payload)

        payload = load_session_run(repo_paths, "session-626")

        assert payload == global_payload


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
