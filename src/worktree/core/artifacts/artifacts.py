"""Artifacts domain entrypoint coordinator."""

from __future__ import annotations

from pathlib import Path

from worktree.core.artifacts.models import (
    ArtifactDownloadResult,
    ArtifactsListResult,
    ArtifactsListStatus,
    ArtifactsPruneResult,
    ArtifactUploadResult,
)
from worktree.core.artifacts.services.download import download_artifact
from worktree.core.artifacts.services.prune import prune_artifacts
from worktree.core.artifacts.services.upload import publish_artifact
from worktree.core.config.models import WorktreeConfig
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.project.services.storage import resolve_project_filesystem_paths


class Artifacts:
    """Unified entrypoint for publishing, listing, downloading, and pruning session artifacts."""

    def __init__(self, path: Path = Path("."), db: ArtifactsRepository | None = None) -> None:
        self.path = path.resolve()
        self.db = db if db is not None else ArtifactsRepository(self.path)

    def upload(
        self,
        session_id: str,
        name: str,
        path_glob: str,
        *,
        sandbox_path: Path,
        retention_days: int | None = None,
    ) -> ArtifactUploadResult:
        """Publish a named artifact bundle matching path_glob from sandbox_path into persistent storage."""
        artifacts_dir = resolve_project_filesystem_paths(self.path).artifacts_dir
        return publish_artifact(
            sandbox_path,
            artifacts_dir,
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
        artifacts_dir = resolve_project_filesystem_paths(self.path).artifacts_dir
        return download_artifact(artifacts_dir, self.db, session_id=session_id, name=name, dest=dest)

    def prune(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        config: WorktreeConfig | None = None,
    ) -> ArtifactsPruneResult:
        """Delete expired artifact bundles from disk and the database, or preview under dry_run.

        remove_expired resolves from config.prune.remove_expired_artifacts, defaulting to False when config is None.
        """
        artifacts_dir = resolve_project_filesystem_paths(self.path).artifacts_dir
        remove_expired = config.prune.remove_expired_artifacts if config is not None else False
        return prune_artifacts(
            self.path, artifacts_dir, self.db, dry_run=dry_run, force=force, remove_expired=remove_expired
        )
