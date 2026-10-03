"""Shared fixtures for engine/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db import RunsRepository
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


@pytest.fixture
def engine_workspace(tmp_path: Path) -> Path:
    """Workspace root with an initialized database."""
    return WorkspaceBuilder(tmp_path / "workspace").with_database().build()


@pytest.fixture
def engine_paths(engine_workspace: Path) -> WorkspacePaths:
    """WorkspacePaths resolved for engine_workspace."""
    return _paths_for(engine_workspace)


@pytest.fixture
def runs_repo(engine_paths: WorkspacePaths) -> RunsRepository:
    """RunsRepository bound to engine_paths' project database."""
    return RunsRepository(db_path=engine_paths.database_file, project_id=engine_paths.project_id)


@pytest.fixture
def artifacts_repository(engine_paths: WorkspacePaths) -> ArtifactsRepository:
    """ArtifactsRepository bound to engine_paths' project database."""
    return ArtifactsRepository(db_path=engine_paths.database_file, project_id=engine_paths.project_id)
