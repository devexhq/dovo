"""Contract tests for dovo.core.bootstrap.services.initialize.initialize_workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.bootstrap.models import BootstrapOutcome, InitFailureMode
from dovo.core.bootstrap.services.initialize import initialize_workspace
from dovo.core.db.connection import resolve_db_path
from dovo.core.project.models import ProjectIdentityProvisionStatus
from tests.harness.builders import WorkspaceBuilder


class InitializeWorkspaceTests:
    """Contract tests for initialize_workspace preflight and idempotency."""

    def test_initialize_workspace_outside_git_repo_sets_preflight_failure_mode(self, tmp_path: Path) -> None:
        """initialize_workspace: non-git root returns ok=False, failure_mode=PREFLIGHT, bootstrap_result=None, one error naming 'not a valid Git repository'."""
        result = initialize_workspace(tmp_path)

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.PREFLIGHT
        assert result.bootstrap_result is None
        assert len(result.errors) == 1
        assert "not a valid Git repository" in result.errors[0]

    def test_initialize_workspace_outside_git_repo_creates_no_dovo_directory(self, tmp_path: Path) -> None:
        """initialize_workspace: non-git root leaves no '.dovo' directory on disk after the call."""
        initialize_workspace(tmp_path)

        assert not (tmp_path / ".dovo").exists()

    def test_initialize_workspace_idempotent_rerun_preserves_custom_setting(self, tmp_path: Path) -> None:
        """initialize_workspace: rerunning on an initialized git workspace with a custom config.json value returns ok=True, bootstrap_result.outcome=ALREADY_INITIALIZED, and leaves the custom value unchanged."""
        workspace = WorkspaceBuilder(tmp_path / "workspace").with_git().build()
        config_path = workspace / ".dovo" / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "custom-project-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = initialize_workspace(workspace)

        assert result.ok is True
        assert result.bootstrap_result is not None
        assert result.bootstrap_result.outcome == BootstrapOutcome.ALREADY_INITIALIZED
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["project"]["name"] == "custom-project-name"

    def test_initialize_workspace_fresh_repo_creates_identity_with_generated_slug(self, git_repo: Path) -> None:
        """initialize_workspace: fresh git repo without --id creates a project identity, status=CREATED."""
        result = initialize_workspace(git_repo)

        assert result.ok is True
        assert result.identity_result is not None
        assert result.identity_result.status == ProjectIdentityProvisionStatus.CREATED
        assert (git_repo / ".dovo" / "project.json").exists()

    def test_initialize_workspace_rerun_without_force_preserves_identity(self, git_repo: Path) -> None:
        """initialize_workspace: rerunning without --id/--force preserves the previously generated identity."""
        first = initialize_workspace(git_repo)
        assert first.identity_result is not None
        assert first.identity_result.identity is not None
        original_id = first.identity_result.identity.id

        second = initialize_workspace(git_repo)

        assert second.ok is True
        assert second.identity_result is not None
        assert second.identity_result.status == ProjectIdentityProvisionStatus.PRESERVED
        assert second.identity_result.identity is not None
        assert second.identity_result.identity.id == original_id

    def test_initialize_workspace_rerun_with_id_and_force_overwrites_identity(self, git_repo: Path) -> None:
        """initialize_workspace: rerunning with --id and --force overwrites the previous identity."""
        initialize_workspace(git_repo)

        result = initialize_workspace(git_repo, project_id="new-id", force=True)

        assert result.ok is True
        assert result.identity_result is not None
        assert result.identity_result.status == ProjectIdentityProvisionStatus.OVERWRITTEN
        assert result.identity_result.identity is not None
        assert result.identity_result.identity.id == "new-id"

    def test_initialize_workspace_invalid_project_id_sets_invalid_project_id_failure_mode(self, git_repo: Path) -> None:
        """initialize_workspace: a malformed --id sets failure_mode=INVALID_PROJECT_ID and skips config generation."""
        result = initialize_workspace(git_repo, project_id="Bad Id!")

        assert result.ok is False
        assert result.failure_mode == InitFailureMode.INVALID_PROJECT_ID
        assert result.config_result is None
        assert not (git_repo / ".dovo" / "project.json").exists()

    def test_initialize_workspace_fresh_repo_creates_only_meta_subdir(self, git_repo: Path) -> None:
        """initialize_workspace: fresh git repo, bootstrap_result.dirs_created == [dovo_dir / '.meta'], no sessions/artifacts/tmp/logs directories created."""
        result = initialize_workspace(git_repo)

        assert result.bootstrap_result is not None
        assert result.bootstrap_result.dirs_created == [result.bootstrap_result.root_path / ".meta"]
        for legacy_dir in ("sessions", "artifacts", "tmp", "logs"):
            assert not (result.bootstrap_result.root_path / legacy_dir).exists()

    def test_initialize_workspace_rerun_preserves_legacy_runtime_dirs_and_contents(self, git_repo: Path) -> None:
        """initialize_workspace: rerun on a workspace with a pre-existing .dovo/sessions/existing.json leaves that file's bytes and the directory unchanged."""
        initialize_workspace(git_repo)
        legacy_file = git_repo / ".dovo" / "sessions" / "existing.json"
        legacy_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_file.write_text('{"a": 1}', encoding="utf-8")

        initialize_workspace(git_repo)

        assert legacy_file.read_text(encoding="utf-8") == '{"a": 1}'

    def test_initialize_workspace_builds_paths_from_provisioned_identity_without_rereading_it(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """initialize_workspace: with the identity loader patched to raise after provisioning, a fresh repository still initializes and the database file is created under the global data dir."""

        def _unexpected_load(*args: object, **kwargs: object) -> None:
            raise AssertionError("identity must not be re-read")

        monkeypatch.setattr("dovo.core.project.services.storage.load_project_identity", _unexpected_load)

        result = initialize_workspace(git_repo)

        assert result.ok is True
        assert resolve_db_path(resolve_global_paths(None)).is_file()
