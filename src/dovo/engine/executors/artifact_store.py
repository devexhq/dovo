"""Session-bound handle on artifact storage used by the step executor."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from dovo.core.artifacts.models import ArtifactProvenance, ArtifactReferencesResult, ArtifactUploadResult
from dovo.core.artifacts.services.references import resolve_artifact_references
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository


class ArtifactStore:
    """Session-bound handle on artifact storage: one object replaces the artifacts_dir/artifacts_db/session_id triple."""

    def __init__(self, artifacts_dir: Path, db: ArtifactsRepository, session_id: str) -> None:
        self.artifacts_dir = artifacts_dir
        self.db = db
        self.session_id = session_id

    @classmethod
    def from_context(
        cls, artifacts_dir: Path | None, db: ArtifactsRepository | None, session_id: str
    ) -> ArtifactStore | None:
        """Return a store when both artifacts_dir and db exist, else None (no active session)."""
        if artifacts_dir is None or db is None:
            return None
        return cls(artifacts_dir, db, session_id)

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
        return publish_artifact(
            worktree_path,
            self.artifacts_dir,
            self.db,
            session_id=self.session_id,
            name=name,
            path_glob=path_glob,
            retention_days=retention_days,
            provenance=provenance,
        )

    def resolve_references(self, requested: Mapping[str, Collection[str]]) -> ArtifactReferencesResult:
        """Resolve requested artifact placeholder fields against this session's latest revisions."""
        return resolve_artifact_references(self.db, session_id=self.session_id, requested=requested)
