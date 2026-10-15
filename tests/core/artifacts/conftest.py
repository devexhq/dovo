"""Shared fixtures for core/artifacts/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


@pytest.fixture
def artifacts_paths(tmp_path: Path) -> WorkspacePaths:
    """Return WorkspacePaths for a freshly initialized, project-scoped workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "artifacts_repo_workspace").with_database().build()
    return initialized_workspace_paths(workspace_root)


@pytest.fixture
def artifacts_repository(artifacts_paths: WorkspacePaths) -> ArtifactsRepository:
    """ArtifactsRepository bound to artifacts_paths' project database."""
    return ArtifactsRepository(db_path=artifacts_paths.database_file, project_id=artifacts_paths.project_id)
