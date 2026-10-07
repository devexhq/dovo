"""Shared fixtures for engine/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionsRepository
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


@pytest.fixture
def engine_workspace(tmp_path: Path) -> Path:
    """Workspace root with an initialized database."""
    return WorkspaceBuilder(tmp_path / "workspace").with_database().build()


@pytest.fixture
def engine_paths(engine_workspace: Path) -> WorkspacePaths:
    """WorkspacePaths resolved for engine_workspace."""
    return _paths_for(engine_workspace)


@pytest.fixture
def sessions_repo(engine_paths: WorkspacePaths) -> SessionsRepository:
    """SessionsRepository bound to engine_paths' project database."""
    return SessionsRepository(db_path=engine_paths.database_file, project_id=engine_paths.project_id)


@pytest.fixture
def artifacts_repository(engine_paths: WorkspacePaths) -> ArtifactsRepository:
    """ArtifactsRepository bound to engine_paths' project database."""
    return ArtifactsRepository(db_path=engine_paths.database_file, project_id=engine_paths.project_id)
