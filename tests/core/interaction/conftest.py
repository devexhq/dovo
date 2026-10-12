"""Shared fixtures for core/interaction/ domain tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths

PublishArtifact = Callable[[str, dict[str, str]], None]


@pytest.fixture
def artifact_store(tmp_path: Path) -> ArtifactStore:
    """ArtifactStore for session 'wf_1' over a freshly initialized workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "interaction_workspace").with_database().build()
    paths: WorkspacePaths = initialized_workspace_paths(workspace_root)
    repository = ArtifactsRepository(db_path=paths.database_file, project_id=paths.project_id)
    return ArtifactStore(Artifacts(paths, repository), "wf_1")


@pytest.fixture
def publish_artifact(artifact_store: ArtifactStore, tmp_path: Path) -> PublishArtifact:
    """Publish a named artifact holding the given files as the store's next revision."""
    counter = {"count": 0}

    def publish(name: str, files: dict[str, str]) -> None:
        counter["count"] += 1
        worktree = tmp_path / f"worktree-{counter['count']}"
        worktree.mkdir()
        for relative_path, content in files.items():
            (worktree / relative_path).write_text(content, encoding="utf-8")
        result = artifact_store.publish(
            worktree, name=name, path_glob="*", retention_days=None, provenance=ArtifactProvenance()
        )
        assert result.ok

    return publish
