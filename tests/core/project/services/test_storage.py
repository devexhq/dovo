"""Tests for project-aware runtime storage resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.project.services.storage import resolve_workspace_paths


class ResolveWorkspacePathsTests:
    """Integration tests for resolving project-aware workspace paths."""

    def test_resolve_workspace_paths_with_persisted_identity_returns_global_runtime_paths(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_workspace_paths: valid project.json with id 'project-626' -> runtime_root equals global_paths.storage_dir / 'projects' / 'project-626'."""
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

    def test_resolve_workspace_paths_no_project_identity_uses_dovo_dir_as_runtime_root(self, tmp_path: Path) -> None:
        """[tier-1/unit] resolve_workspace_paths: no project.json present -> runtime_root equals repository_paths.dovo_dir and project_id is None."""
        repository = tmp_path / "repository"

        paths = resolve_workspace_paths(
            RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global")
        )

        dovo_dir = repository / ".dovo"
        assert paths.project_id is None
        assert paths.runtime_root == dovo_dir
        assert paths.sessions_dir == dovo_dir / "sessions"
        assert paths.artifacts_dir == dovo_dir / "artifacts"
        assert paths.logs_dir == dovo_dir / "logs"
        assert paths.tmp_dir == dovo_dir / "tmp"
        assert paths.worktrees_dir == dovo_dir / "worktrees"

    def test_resolve_workspace_paths_with_undecodable_identity_uses_dovo_dir_as_runtime_root(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/unit] resolve_workspace_paths: invalid UTF-8 identity bytes are treated as no identity, not raised."""
        repository = tmp_path / "repository"
        identity_path = repository / ".dovo" / "project.json"
        identity_path.parent.mkdir(parents=True)
        identity_path.write_bytes(b"\xff\xfe\x00invalid")

        paths = resolve_workspace_paths(
            RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global")
        )

        dovo_dir = repository / ".dovo"
        assert paths.project_id is None
        assert paths.sessions_dir == dovo_dir / "sessions"
        assert paths.artifacts_dir == dovo_dir / "artifacts"
        assert paths.logs_dir == dovo_dir / "logs"
        assert paths.tmp_dir == dovo_dir / "tmp"
