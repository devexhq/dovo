"""Session-bound view of the project's artifact storage."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from dovo.core.artifacts.artifacts import Artifacts
from dovo.core.artifacts.models import (
    ArtifactDownloadResult,
    ArtifactProvenance,
    ArtifactReferencesResult,
    ArtifactUploadResult,
)


class ArtifactStore:
    """Session-bound view of Artifacts: one session id over the project's artifact storage."""

    def __init__(self, artifacts: Artifacts, session_id: str) -> None:
        self._artifacts = artifacts
        self._session_id = session_id

    def publish(
        self,
        worktree_path: Path,
        *,
        name: str,
        path_glob: str,
        retention_days: int | None,
        provenance: ArtifactProvenance,
    ) -> ArtifactUploadResult:
        """Publish a named bundle as the next revision for this session."""
        return self._artifacts.publish(
            self._session_id,
            name,
            path_glob,
            worktree_path=worktree_path,
            retention_days=retention_days,
            provenance=provenance,
        )

    def download(
        self,
        *,
        name: str,
        dest: Path,
        revision: int | None = None,
        session_id: str | None = None,
    ) -> ArtifactDownloadResult:
        """Download a named revision (latest by default) from session_id, defaulting to this store's session."""
        return self._artifacts.download(
            self._session_id if session_id is None else session_id, name, dest=dest, revision=revision
        )

    def resolve_references(self, requested: Mapping[str, Collection[str]]) -> ArtifactReferencesResult:
        """Resolve requested artifact placeholder fields against this session's latest revisions."""
        return self._artifacts.resolve_references(self._session_id, requested)
