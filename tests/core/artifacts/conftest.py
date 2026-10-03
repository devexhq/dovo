"""Shared fixtures for core/artifacts/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.project.services.storage import resolve_workspace_paths
from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def artifacts_repository(tmp_path: Path) -> ArtifactsRepository:
    """ArtifactsRepository bound to a freshly initialized, project-scoped workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "artifacts_repo_workspace").with_database().build()
    paths = resolve_workspace_paths(RepositoryPaths.from_root(workspace_root), resolve_global_paths(None))
    return ArtifactsRepository(db_path=paths.database_file, project_id=paths.project_id)
