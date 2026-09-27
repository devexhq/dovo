"""Shared fixtures for core/runtime/ domain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from worktree.core.db.repositories.artifacts import ArtifactsRepository


@pytest.fixture
def artifacts_repository(tmp_path: Path) -> ArtifactsRepository:
    """ArtifactsRepository bound to a freshly initialized, project-scoped workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "runtime_artifacts_workspace").with_database().build()
    return ArtifactsRepository(workspace_root)
