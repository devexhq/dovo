"""Tests for the Status entrypoint: full-result assembly, warning order and remediation fixes."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.db import SessionsRepository, SessionStatus, WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.status import Status
from tests.harness.status_workspace import BuildStatusWorkspace, status_config_payload
from tests.harness.workspace_paths import initialized_workspace_paths


def _tree_listing(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if ".git" not in path.relative_to(root).parts)


class StatusCollectTests:
    """Tests for the full result Status.collect() assembles."""

    def test_collect_healthy_workspace_reports_every_field(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(config=status_config_payload(model="gpt-4o", max_active_worktrees=3))
        fs = Filesystem(workspace)
        paths = initialized_workspace_paths(workspace)

        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "deploy.yml", "name: deploy\n")
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "lint-blueprint.yml", "name: lint-blueprint\n")
        Filesystem.atomic_write_text(fs.catalog_steps_dir / "test-step.yml", "name: test-step\n")

        sessions_repo = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        sessions_repo.create(
            session_id="sess-001",
            blueprint_name="deploy",
            blueprint_key="deploy",
            status=SessionStatus.COMPLETED,
        )

        worktrees_repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktrees_repo.create(
            id="sb-001",
            branch_name="dovo/sb-001",
            base_commit="HEAD",
            worktree_path=fs.worktrees_dir / "sb-001",
        )

        result = Status(paths, git=GitRunner()).collect()

        assert result.root_dir == workspace
        assert result.is_initialized is True
        assert result.git.is_git_repo is True
        assert result.git.branch == "feature-status"
        assert result.git.is_dirty is False
        assert result.git.uncommitted_files == 0
        assert result.config.status is ConfigLoadStatus.OK
        assert result.config.is_valid is True
        assert result.catalog.exists is True
        assert result.catalog.total_items == 3
        assert result.catalog.blueprints_count == 2
        assert result.catalog.steps_count == 1
        assert result.catalog.invalid_items == 0
        assert result.catalog.item_names == ["deploy", "lint-blueprint", "test-step"]
        assert result.database.exists is True
        assert result.database.db_path == paths.database_file
        assert result.database.is_accessible is True
        assert result.database.total_sessions == 1
        assert result.worktrees.active_worktrees == 1
        assert result.worktrees.total_worktrees == 1
        assert result.worktrees.max_active_worktrees == 3
        assert result.warnings == []
        assert result.fixes == []

    def test_collect_on_plain_directory_writes_nothing(self, tmp_path: Path) -> None:
        paths = initialized_workspace_paths(tmp_path)
        before = _tree_listing(tmp_path)

        Status(paths, git=GitRunner()).collect()

        assert _tree_listing(tmp_path) == before

    def test_collect_on_populated_workspace_leaves_rows_and_files_unchanged(
        self, status_workspace: BuildStatusWorkspace
    ) -> None:
        workspace = status_workspace(config=None)
        fs = Filesystem(workspace)
        paths = initialized_workspace_paths(workspace)
        sessions_repo = SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)
        sessions_repo.create(
            session_id="sess-ro",
            blueprint_name="deploy",
            blueprint_key="deploy",
            status=SessionStatus.COMPLETED,
        )
        worktrees_repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktrees_repo.create(
            id="sb-ro",
            branch_name="dovo/sb-ro",
            base_commit="HEAD",
            worktree_path=fs.worktrees_dir / "sb-ro",
        )
        sessions_before = [record.model_dump() for record in sessions_repo.list()]
        worktrees_before = [record.model_dump() for record in worktrees_repo.list()]
        listing_before = _tree_listing(workspace)

        Status(paths, git=GitRunner()).collect()

        assert [record.model_dump() for record in sessions_repo.list()] == sessions_before
        assert [record.model_dump() for record in worktrees_repo.list()] == worktrees_before
        assert not fs.config_file.exists()
        assert _tree_listing(workspace) == listing_before


class StatusWarningsOrderingTests:
    """Tests verifying deterministic warning priority order."""

    def test_collect_warnings_follow_deterministic_priority(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(branch="main", config=None)
        fs = Filesystem(workspace)
        paths = initialized_workspace_paths(workspace)

        (workspace / "dirty.txt").write_text("dirty content", encoding="utf-8")
        Filesystem.atomic_write_text(fs.catalog_blueprints_dir / "broken.yml", "bad: [yaml\n")

        result = Status(paths, git=GitRunner()).collect()

        assert result.warnings == [
            "Dovo workspace is not initialized. Run 'dovo init' to configure.",
            "Active branch is 'main'. Automated workflows on primary branches are discouraged.",
            "Working tree has 1 uncommitted change(s).",
            "1 invalid blueprint file(s) detected in catalog.",
        ]

        Filesystem.atomic_write_json(
            fs.config_file,
            status_config_payload(model=None, max_active_worktrees=8),
        )

        result2 = Status(paths, git=GitRunner()).collect()

        assert result2.warnings == [
            "Active branch is 'main'. Automated workflows on primary branches are discouraged.",
            "Working tree has 1 uncommitted change(s).",
            "Agent model is not configured (agents.copilot.model is unset).",
            "max_active_worktrees (8) is unusually high.",
            "1 invalid blueprint file(s) detected in catalog.",
        ]


class StatusFixesRemediationTests:
    """Tests for actionable remediation fixes."""

    def test_collect_fixes_for_uninitialized_workspace(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(config=None)

        result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()

        assert result.fixes == ["Run 'dovo init' to initialize Dovo in this repository."]

    def test_collect_fixes_for_non_git_repo(self, tmp_path: Path) -> None:
        non_git_dir = tmp_path / "non_git_repo"
        fs = Filesystem(non_git_dir)
        Filesystem.atomic_write_json(fs.config_file, status_config_payload(model="gpt-4o"))

        result = Status(initialized_workspace_paths(non_git_dir), git=GitRunner()).collect()

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
    def test_collect_fixes_for_invalid_config_payload(
        self,
        status_workspace: BuildStatusWorkspace,
        payload: str,
        expected_fix: str,
    ) -> None:
        workspace = status_workspace(config=payload)

        result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()

        assert result.fixes == [expected_fix]

    def test_collect_fixes_for_path_is_directory(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(config=None)
        fs = Filesystem(workspace)

        fs.config_file.mkdir(parents=True, exist_ok=True)

        result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()

        assert result.fixes == ["Remove directory at .dovo/config.json and run 'dovo init'."]

    def test_collect_fixes_for_unreadable(self, status_workspace: BuildStatusWorkspace) -> None:
        workspace = status_workspace(config="{}")
        fs = Filesystem(workspace)

        fs.config_file.chmod(0)

        try:
            if os.access(fs.config_file, os.R_OK):
                pytest.skip("filesystem still allows reading unreadable mode")
            result = Status(initialized_workspace_paths(workspace), git=GitRunner()).collect()
            assert result.fixes == ["Check file permissions for .dovo/config.json."]
        finally:
            fs.config_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
