"""Tests for run-definitions snapshotting."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.redact import SecretRedactor
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.db import SessionStatus
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.engine.models import DefinitionRef, DefinitionsManifest
from dovo.engine.state_models import SessionJsonPayload, SessionLifecycle
from dovo.engine.writer import (
    get_session_dir,
    load_blueprint_from_snapshot,
    snapshot_definitions,
    write_session_diff,
    write_session_projection,
)
from tests.harness.builders import BlueprintBuilder, StepBuilder
from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from tests.harness.workspace_paths import initialized_workspace_paths

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


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
            .with_use_worktree(False)
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
    """Contract tests for write_session_projection writing session.json."""

    def test_write_session_projection_writes_indented_payload_json_and_returns_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] write_session_projection: returns <session_dir>/session.json whose text equals payload.model_dump_json(indent=2) and that parses back to an equal SessionJsonPayload."""
        payload = SessionJsonPayload(
            revision=2,
            manifest=DefinitionsManifest(
                blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="a", resolved_at="2026-09-28T00:00:00+00:00")
            ),
            lifecycle=SessionLifecycle(status=SessionStatus.RUNNING, started_at="2026-09-28T00:00:00+00:00"),
        )

        path = write_session_projection(tmp_path / "session", payload)

        assert path == tmp_path / "session" / "session.json"
        assert path.read_text(encoding="utf-8") == payload.model_dump_json(indent=2)
        assert SessionJsonPayload.model_validate_json(path.read_text(encoding="utf-8")) == payload


class SessionArtifactWriterTests:
    """Integration tests for creating project-aware session directories and persisting diff artifacts."""

    def test_get_session_dir_with_project_identity_creates_global_session_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """An identified project creates its session directory in global storage."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        save_project_identity(repository / ".dovo" / "project.json", identity)

        session_dir = get_session_dir(workspace_paths_factory(repository, None), "session-626")

        expected_session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "session-626"
        assert session_dir == expected_session_dir
        assert session_dir.is_dir()
        assert not (repository / ".dovo" / "sessions" / "session-626").exists()

    def test_write_session_diff_persists_diff_text_and_returns_the_patch_file_path(self, tmp_path: Path) -> None:
        """write_session_diff: writes diff.patch under the given session directory and returns its path."""
        session_dir = tmp_path / "session-dir"
        session_dir.mkdir()

        target = write_session_diff(session_dir, "diff content", SecretRedactor([]))

        assert target == session_dir / "diff.patch"
        assert target.read_text(encoding="utf-8") == "diff content"

    def test_write_session_diff_persists_masked_copy_and_leaves_input_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] write_session_diff: with ANTHROPIC_API_KEY="sk-ant-secret-value-123", diff_text "+key=sk-ant-secret-value-123\\n" writes "+key=[REDACTED:ANTHROPIC_API_KEY]\\n" to <session_dir>/diff.patch and returns that path."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value-123")
        session_dir = tmp_path / "session-dir"
        session_dir.mkdir()

        target = write_session_diff(session_dir, "+key=sk-ant-secret-value-123\n", SecretRedactor.from_environment())

        assert target == session_dir / "diff.patch"
        assert target.read_text(encoding="utf-8") == "+key=[REDACTED:ANTHROPIC_API_KEY]\n"
