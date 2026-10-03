"""Artifacts domain entrypoint coordinator."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.artifacts.models import (
    ArtifactDownloadResult,
    ArtifactsListResult,
    ArtifactsListStatus,
    ArtifactsPruneResult,
    ArtifactUploadResult,
)
from dovo.core.artifacts.services.download import download_artifact
from dovo.core.artifacts.services.prune import prune_artifacts
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.config.models import DovoConfig
from dovo.core.db.repositories.artifacts import ArtifactsRepository


class Artifacts:
    """Unified entrypoint for publishing, listing, downloading, and pruning session artifacts."""

    def __init__(self, paths: WorkspacePaths, db: ArtifactsRepository | None = None) -> None:
        self.paths = paths
        self.path = paths.root_dir
        self.db = (
            db if db is not None else ArtifactsRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

    def upload(
        self,
        session_id: str,
        name: str,
        path_glob: str,
        *,
        worktree_path: Path,
        retention_days: int | None = None,
    ) -> ArtifactUploadResult:
        """Publish a named artifact bundle matching path_glob from worktree_path into persistent storage."""
        return publish_artifact(
            worktree_path,
            self.paths.artifacts_dir,
            self.db,
            session_id=session_id,
            name=name,
            path_glob=path_glob,
            retention_days=retention_days,
        )

    def list(self, session_id: str | None = None) -> ArtifactsListResult:
        """List artifacts for the current project, optionally filtered to one session."""
        return ArtifactsListResult(status=ArtifactsListStatus.OK, artifacts=self.db.list(session_id=session_id))

    def download(
        self,
        session_id: str,
        name: str,
        *,
        dest: Path,
    ) -> ArtifactDownloadResult:
        """Download and checksum-verify a named artifact bundle into dest."""
        return download_artifact(self.paths.artifacts_dir, self.db, session_id=session_id, name=name, dest=dest)

    def prune(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        config: DovoConfig | None = None,
    ) -> ArtifactsPruneResult:
        """Delete expired artifact bundles from disk and the database, or preview under dry_run.

        remove_expired resolves from config.prune.remove_expired_artifacts, defaulting to False when config is None.
        """
        remove_expired = config.prune.remove_expired_artifacts if config is not None else False
        return prune_artifacts(
            self.path, self.paths.artifacts_dir, self.db, dry_run=dry_run, force=force, remove_expired=remove_expired
        )
