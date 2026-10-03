"""Tests for dovo.core.status.collector and Status facade."""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.config.models import AgentConfig, DovoConfig, ProjectConfig, WorktreeConfig
from dovo.core.db import RunsRepository, RunStatus, WorktreesRepository
from dovo.core.db.connection import resolve_db_path
from dovo.core.git import GitNotFoundError, GitPlumbingTimeoutError, GitRunner
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.status import DovoStatusResult, Status
from dovo.core.status.services.collector import collect_status
from tests.harness import WorkspaceBuilder


def _status_paths(workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for a WorkspaceBuilder-scaffolded workspace root."""
    return resolve_workspace_paths(RepositoryPaths.from_root(workspace), resolve_global_paths(None))


def _config_payload(*, model: str | None = "gpt-4o", max_active_worktrees: int = 5) -> dict[str, Any]:
    return DovoConfig(
        version=1,
        project=ProjectConfig(name="status-ws"),
        agent=AgentConfig(model=model),
        worktree=WorktreeConfig(max_active_worktrees=max_active_worktrees),
    ).model_dump(mode="json")


class StatusFacadeTests:
    """Tests for Status domain facade."""

    @pytest.mark.parametrize(
        "invoke",
        [
            pytest.param(lambda p: Status(p).collect(), id="instance"),
            pytest.param(Status.collect_at, id="classmethod"),
        ],
    )
    def test_status_collect_returns_dovo_status_result(
        self,
        tmp_path: Path,
        invoke: Callable[[WorkspacePaths], DovoStatusResult],
    ) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "facade_collect")
            .with_git(branch="feature-facade")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))
        paths = _status_paths(workspace)

        result = invoke(paths)

        assert result.root_dir == workspace
        assert result.is_initialized is True
        assert result.git.is_git_repo is True
        assert result.git.branch == "feature-facade"


class StatusCollectorGitCollectionTests:
    """Tests for git repository status collection in collect_status."""

    def test_collect_status_clean_worktree(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "clean_ws")
            .with_git(branch="feature-status")
            .with_database()
            .without_catalog_templates()
            .build()
        )
        paths = _status_paths(workspace)
        fs = Filesystem(workspace)
        config_data = _config_payload(model="gpt-4o", max_active_worktrees=3)
        Filesystem.atomic_write_json(fs.config_file, config_data)
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "deploy.yml", "name: deploy\n")
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "lint-blueprint.yml", "name: lint-blueprint\n")
        Filesystem.atomic_write_text(fs.catalog_steps_dir / "test-step.yml", "name: test-step\n")

        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        runs_repo.create(
            session_id="sess-001",
            blueprint_name="deploy",
            blueprint_key="deploy",
            status=RunStatus.COMPLETED,
        )

        worktrees_repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktrees_repo.create(
            id="sb-001",
            branch_name="dovo/sb-001",
            base_commit="HEAD",
            worktree_path=fs.worktrees_dir / "sb-001",
        )

        result = collect_status(paths)

        assert result.is_initialized is True
        assert result.git.branch == "feature-status"
        assert result.git.is_dirty is False
        assert result.config.is_valid is True
        assert result.catalog.exists is True
        assert result.catalog.total_items == 3
        assert result.catalog.steps_count == 1
        assert result.catalog.item_names == ["deploy", "lint-blueprint", "test-step"]
        assert result.database.exists is True
        assert result.database.is_accessible is True
        assert result.database.total_runs == 1
        assert result.worktrees.active_worktrees == 1
        assert result.worktrees.total_worktrees == 1
        assert result.worktrees.max_active_worktrees == 3
        assert result.warnings == []
        assert result.fixes == []

    def test_collect_status_dirty_worktree(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "dirty_ws")
            .with_git(branch="feature-dirty")
            .with_database()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        untracked = workspace / "new_file.txt"
        untracked.write_text("hello", encoding="utf-8")

        result = collect_status(_status_paths(workspace))

        assert result.git.branch == "feature-dirty"
        assert result.git.is_dirty is True
        assert result.git.uncommitted_files == 1
        assert result.warnings == ["Working tree has 1 uncommitted change(s)."]

    def test_collect_status_detached_head(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "detached_ws")
            .with_git(branch="feature-detached")
            .with_database()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        commit_hash = GitRunner.run(["rev-parse", "HEAD"], path=workspace).strip()
        subprocess.run(["git", "checkout", commit_hash], cwd=workspace, check=True, capture_output=True)

        result = collect_status(_status_paths(workspace))

        assert result.git.branch == "HEAD (detached)"
        assert result.git.is_git_repo is True

    def test_collect_status_non_git_directory(self, tmp_path: Path) -> None:
        non_git_dir = tmp_path / "non_git"
        non_git_dir.mkdir(parents=True, exist_ok=True)

        result = collect_status(_status_paths(non_git_dir))

        assert result.is_initialized is False
        assert result.git.is_git_repo is False
        assert result.git.branch == "none"
        assert result.config.is_valid is False
        assert result.database.exists is False
        assert result.warnings == ["Dovo workspace is not initialized. Run 'dovo init' to configure."]
        assert result.fixes == [
            "Run 'dovo init' to initialize Dovo in this repository.",
            "Run 'git init' or navigate to a Git repository.",
        ]

    @pytest.mark.parametrize(
        "git_error",
        [
            pytest.param(GitNotFoundError("git not found"), id="not_found"),
            pytest.param(GitPlumbingTimeoutError("git timed out"), id="timeout"),
        ],
    )
    def test_collect_status_git_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        git_error: Exception,
    ) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "git_error_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        def mock_run(*args: object, **kwargs: object) -> str:
            raise git_error

        monkeypatch.setattr(GitRunner, "run", mock_run)

        result = collect_status(_status_paths(workspace))

        assert result.git.is_git_repo is False
        assert result.git.branch == "unknown"
        assert result.fixes == ["Run 'git init' or navigate to a Git repository."]

    def test_collect_status_git_rev_parse_not_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "git_false_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        config_data = _config_payload(model="gpt-4o")
        Filesystem.atomic_write_json(fs.config_file, config_data)

        def mock_run(*args: object, **kwargs: object) -> str:
            return "false"

        monkeypatch.setattr(GitRunner, "run", mock_run)

        result = collect_status(_status_paths(workspace))

        assert result.git.is_git_repo is False
        assert result.git.branch == "none"
        assert result.fixes == ["Run 'git init' or navigate to a Git repository."]


class StatusCollectorConfigAndCatalogTests:
    """Tests for configuration and catalog status collection in collect_status."""

    def test_collect_status_uninitialized_workspace(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "uninit_ws")
            .with_git(branch="feature-uninit")
            .without_config()
            .without_catalog_templates()
            .build()
        )
        result = collect_status(_status_paths(workspace))

        assert result.is_initialized is False
        assert result.git.branch == "feature-uninit"
        assert result.config.is_valid is False
        assert result.warnings == ["Dovo workspace is not initialized. Run 'dovo init' to configure."]
        assert result.fixes == ["Run 'dovo init' to initialize Dovo in this repository."]

    def test_collect_status_malformed_config(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "malformed_config_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_text(fs.config_file, "{invalid_json: true")

        result = collect_status(_status_paths(workspace))

        assert result.config.status == ConfigLoadStatus.MALFORMED_JSON
        assert result.config.is_valid is False
        assert any("CONFIG_MALFORMED_JSON" in err for err in result.config.errors)
        assert result.config.fixes == ["Repair JSON syntax, or restore from backup"]
        assert any("Malformed config.json" in w for w in result.warnings)
        assert result.fixes == ["Repair JSON syntax in .dovo/config.json or restore from backup."]

    def test_collect_status_missing_catalog_directory(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "missing_catalog_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        result = collect_status(_status_paths(workspace))

        assert result.is_initialized is True
        assert result.catalog.exists is False
        assert result.catalog.total_items == 0

    def test_collect_status_invalid_catalog_blueprint(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "invalid_catalog_bp_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "valid-bp.yml", "name: valid-bp\n")
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "bad.yml", "invalid: [yaml: broken\n")

        result = collect_status(_status_paths(workspace))

        assert result.catalog.exists is True
        assert result.catalog.total_items == 2
        assert result.catalog.invalid_items == 1
        assert result.catalog.item_names == ["bad", "valid-bp"]
        assert result.warnings == ["1 invalid blueprint file(s) detected in catalog."]


class StatusCollectorDatabaseAndWorktreeTests:
    """Tests for database and worktree status collection in collect_status."""

    def test_collect_status_missing_database(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "missing_db_ws")
            .with_git(branch="feature-status")
            .without_database()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        result = collect_status(_status_paths(workspace))

        assert result.database.exists is False
        assert result.database.is_accessible is False

    def test_collect_status_corrupted_database(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "corrupted_db_ws")
            .with_git(branch="feature-status")
            .with_database()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))
        resolve_db_path(resolve_global_paths(None)).write_bytes(b"NOT A SQLITE DATABASE")

        result = collect_status(_status_paths(workspace))

        assert result.database.exists is True
        assert result.database.is_accessible is False
        assert result.database.total_runs == 0

    def test_collect_status_worktrees_directory_fallback(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "worktree_fallback_ws")
            .with_git(branch="feature-status")
            .without_database()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        config_payload = _config_payload(model="gpt-4o", max_active_worktrees=4)
        Filesystem.atomic_write_json(fs.config_file, config_payload)
        (fs.worktrees_dir / "sb-1").mkdir(parents=True, exist_ok=True)
        (fs.worktrees_dir / "sb-2").mkdir(parents=True, exist_ok=True)

        result = collect_status(_status_paths(workspace))

        assert result.database.exists is False
        assert result.worktrees.active_worktrees == 2
        assert result.worktrees.total_worktrees == 2
        assert result.worktrees.max_active_worktrees == 4

    def test_collect_status_worktrees_db_query_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "worktree_db_error_ws")
            .with_git(branch="feature-status")
            .with_database()
            .without_catalog_templates()
            .build()
        )
        paths = _status_paths(workspace)
        fs = Filesystem(workspace)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        runs_repo = RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        runs_repo.create(
            session_id="sess-test",
            blueprint_name="test-bp",
            blueprint_key="test-bp",
            status=RunStatus.COMPLETED,
        )
        (fs.worktrees_dir / "sb-fallback").mkdir(parents=True, exist_ok=True)

        def mock_list(*args: object, **kwargs: object) -> list[object]:
            raise RuntimeError("DB query failure")

        monkeypatch.setattr(WorktreesRepository, "list", mock_list)

        result = collect_status(paths)

        assert result.database.total_runs == 1
        assert result.worktrees.active_worktrees == 1
        assert result.worktrees.total_worktrees == 1


class StatusCollectorWarningsOrderingTests:
    """Tests verifying deterministic warning priority order in collect_status."""

    def test_collect_status_warnings_follow_deterministic_priority(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "warnings_order_ws")
            .with_git(branch="main")
            .without_config()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        paths = _status_paths(workspace)

        (workspace / "dirty.txt").write_text("dirty content", encoding="utf-8")
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "broken.yml", "bad: [yaml\n")

        result = collect_status(paths)

        assert result.warnings == [
            "Dovo workspace is not initialized. Run 'dovo init' to configure.",
            "Active branch is 'main'. Automated workflows on primary branches are discouraged.",
            "Working tree has 1 uncommitted change(s).",
            "1 invalid blueprint file(s) detected in catalog.",
        ]

        Filesystem.atomic_write_json(
            fs.config_file,
            _config_payload(model=None, max_active_worktrees=8),
        )

        result2 = collect_status(paths)

        assert result2.warnings == [
            "Active branch is 'main'. Automated workflows on primary branches are discouraged.",
            "Working tree has 1 uncommitted change(s).",
            "Agent model is not configured (agent.model is null).",
            "max_active_worktrees (8) is unusually high.",
            "1 invalid blueprint file(s) detected in catalog.",
        ]


class StatusCollectorFixesRemediationTests:
    """Tests for actionable remediation fixes in collect_status."""

    def test_collect_status_fixes_for_uninitialized_workspace(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "uninit_fixes_ws")
            .with_git(branch="feature-status")
            .without_config()
            .without_catalog_templates()
            .build()
        )

        result = collect_status(_status_paths(workspace))

        assert result.fixes == ["Run 'dovo init' to initialize Dovo in this repository."]

    def test_collect_status_fixes_for_non_git_repo(self, tmp_path: Path) -> None:
        non_git_dir = tmp_path / "non_git_repo"
        fs = Filesystem(non_git_dir)
        Filesystem.atomic_write_json(fs.config_file, _config_payload(model="gpt-4o"))

        result = collect_status(_status_paths(non_git_dir))

        assert result.fixes == ["Run 'git init' or navigate to a Git repository."]

    @pytest.mark.parametrize(
        ("payload", "expected_fix"),
        [
            pytest.param(
                "{bad json: true",
                "Repair JSON syntax in .dovo/config.json or restore from backup.",
                id="malformed_json",
            ),
            pytest.param(
                "{}",
                "Run 'dovo config validate' to inspect schema errors or 'dovo init --repair' to insert missing keys.",
                id="schema_invalid",
            ),
            pytest.param(
                '["not", "an", "object"]',
                "Ensure .dovo/config.json contains a JSON object root.",
                id="root_not_object",
            ),
        ],
    )
    def test_collect_status_fixes_for_invalid_config_payload(
        self,
        tmp_path: Path,
        payload: str,
        expected_fix: str,
    ) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "invalid_config_fixes_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_text(fs.config_file, payload)

        result = collect_status(_status_paths(workspace))

        assert result.fixes == [expected_fix]

    def test_collect_status_fixes_for_path_is_directory(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "path_is_dir_fixes_ws")
            .with_git(branch="feature-status")
            .without_config()
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        fs.config_file.mkdir(parents=True, exist_ok=True)

        result = collect_status(_status_paths(workspace))

        assert result.fixes == ["Remove directory at .dovo/config.json and run 'dovo init'."]

    def test_collect_status_fixes_for_unreadable(self, tmp_path: Path) -> None:
        workspace = (
            WorkspaceBuilder(tmp_path / "unreadable_fixes_ws")
            .with_git(branch="feature-status")
            .without_catalog_templates()
            .build()
        )
        fs = Filesystem(workspace)
        Filesystem.atomic_write_text(fs.config_file, "{}")
        fs.config_file.chmod(0)

        try:
            if os.access(fs.config_file, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")
            result = collect_status(_status_paths(workspace))
            assert result.fixes == ["Check file permissions for .dovo/config.json."]
        finally:
            fs.config_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
