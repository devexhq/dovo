"""Shared fixtures for core/artifacts/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.db.repositories.artifacts import ArtifactsRepository
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


@pytest.fixture
def artifacts_repository(tmp_path: Path) -> ArtifactsRepository:
    """ArtifactsRepository bound to a freshly initialized, project-scoped workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "artifacts_repo_workspace").with_database().build()
    paths = initialized_workspace_paths(workspace_root)
    return ArtifactsRepository(db_path=paths.database_file, project_id=paths.project_id)
