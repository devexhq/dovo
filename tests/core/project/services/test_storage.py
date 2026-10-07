"""Tests for project-aware runtime storage resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspaceNotInitializedError
from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.project.services.storage import build_workspace_paths, resolve_workspace_paths

FORCE_HINT = "Or run `dovo init --id <project-id> --force` to replace the unusable identity."


class ResolveWorkspacePathsTests:
    """Contract tests for resolving project-aware workspace paths."""

    def test_missing_identity_raises_and_creates_nothing(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_workspace_paths: no project.json raises WorkspaceNotInitializedError whose text equals the two-line pre-determined message naming <repo>/.dovo/project.json with no '--force', and <repo>/.dovo is not created."""
        repository = tmp_path / "repository"

        with pytest.raises(WorkspaceNotInitializedError) as exc_info:
            resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global"))

        identity_path = repository / ".dovo" / "project.json"
        assert str(exc_info.value) == (
            f"Workspace is not initialized: no valid project identity at '{identity_path}' (WORKSPACE_NOT_INITIALIZED).\n"
            "Fix: Run `dovo init` to initialize this workspace."
        )
        assert not (repository / ".dovo").exists()

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"\xff\xfe\x00invalid", id="undecodable"),
            pytest.param(b"not json", id="malformed"),
            pytest.param(b'{"id": 5}', id="schema-invalid"),
        ],
    )
    def test_unusable_identity_raises_with_force_hint(self, tmp_path: Path, content: bytes) -> None:
        """[tier-1/unit] resolve_workspace_paths: each undecodable, malformed, or schema-invalid project.json raises WorkspaceNotInitializedError whose text ends with the --force replacement hint."""
        repository = tmp_path / "repository"
        identity_path = repository / ".dovo" / "project.json"
        identity_path.parent.mkdir(parents=True)
        identity_path.write_bytes(content)

        with pytest.raises(WorkspaceNotInitializedError) as exc_info:
            resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global"))

        assert str(exc_info.value).endswith(FORCE_HINT)

    def test_valid_identity_resolves_runtime_under_global_storage(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_workspace_paths: id 'project-626' returns project_id 'project-626' and runtime_root == <global>/storage/projects/project-626 with logs, sessions, artifacts, tmp as its same-named children and worktrees_dir == <repo>/.dovo/worktrees."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        save_project_identity(repository / ".dovo" / "project.json", identity)

        paths = resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(global_root))

        project_storage = global_root / "storage" / "projects" / "project-626"
        assert paths.project_id == "project-626"
        assert paths.runtime_root == project_storage
        assert paths.sessions_dir == project_storage / "sessions"
        assert paths.artifacts_dir == project_storage / "artifacts"
        assert paths.logs_dir == project_storage / "logs"
        assert paths.tmp_dir == project_storage / "tmp"
        assert paths.worktrees_dir == repository / ".dovo" / "worktrees"


class BuildWorkspacePathsTests:
    """Contract tests for the pure workspace paths builder."""

    def test_build_workspace_paths_is_pure_and_matches_resolve(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_workspace_paths: for id 'project-p' it returns the same WorkspacePaths as resolve_workspace_paths for a persisted identity 'p' and creates no file or directory."""
        repository = tmp_path / "repository"
        repository_paths = RepositoryPaths.from_root(repository)
        global_paths = GlobalPaths.from_root(tmp_path / "global")

        built = build_workspace_paths(repository_paths, global_paths, "project-p")

        assert not repository.exists()
        assert not (tmp_path / "global").exists()
        save_project_identity(
            repository / ".dovo" / "project.json",
            ProjectIdentity(id="project-p", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
        )
        assert resolve_workspace_paths(repository_paths, global_paths) == built
