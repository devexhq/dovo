"""Shared fixtures for core/artifacts/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from worktree.common.filesystem.models import RepositoryPaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.project.services.storage import resolve_workspace_paths


@pytest.fixture
def artifacts_repository(tmp_path: Path) -> ArtifactsRepository:
    """ArtifactsRepository bound to a freshly initialized, project-scoped workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "artifacts_repo_workspace").with_database().build()
    paths = resolve_workspace_paths(RepositoryPaths.from_root(workspace_root), resolve_global_paths(None))
    return ArtifactsRepository(db_path=paths.database_file, project_id=paths.project_id)
