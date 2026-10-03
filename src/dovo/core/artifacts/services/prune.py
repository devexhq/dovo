"""Prune service: delete expired artifact bundles from disk and the database."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.artifacts.models import ArtifactsPruneResult, ArtifactsPruneStatus, PrunedArtifact
from dovo.core.db.models import ArtifactRecord
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _prune_one_artifact(
    record: ArtifactRecord, artifacts_dir: Path, db: ArtifactsRepository, *, dry_run: bool
) -> PrunedArtifact:
    """Delete one expired artifact's directory and DB row (or report what would be deleted under dry_run), never raising."""
    if dry_run:
        return PrunedArtifact(session_id=record.session_id, name=record.name, pruned=True)

    try:
        if record.path.exists():
            shutil.rmtree(record.path)
        db.delete(record.session_id, record.name)
    except OSError as exc:
        return PrunedArtifact(session_id=record.session_id, name=record.name, pruned=False, error=str(exc))

    return PrunedArtifact(session_id=record.session_id, name=record.name, pruned=True)


def prune_artifacts(
    workspace_root: Path,
    artifacts_dir: Path,
    db: ArtifactsRepository,
    *,
    dry_run: bool = False,
    force: bool = False,
    remove_expired: bool,
) -> ArtifactsPruneResult:
    """List expired artifacts via db.list_expired and delegate each to _prune_one_artifact, guarded by WorkspaceLock(workspace_root) unless dry_run."""
    if not remove_expired and not force:
        return ArtifactsPruneResult(status=ArtifactsPruneStatus.DISABLED, dry_run=dry_run, force=force)

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    if dry_run:
        items = [_prune_one_artifact(record, artifacts_dir, db, dry_run=True) for record in db.list_expired(now)]
        return ArtifactsPruneResult(status=ArtifactsPruneStatus.OK, dry_run=dry_run, force=force, items=items)

    try:
        with WorkspaceLock(RepositoryPaths.from_root(workspace_root).lock_file):
            items = [_prune_one_artifact(record, artifacts_dir, db, dry_run=False) for record in db.list_expired(now)]
            return ArtifactsPruneResult(status=ArtifactsPruneStatus.OK, dry_run=dry_run, force=force, items=items)
    except LockTimeoutError as exc:
        return ArtifactsPruneResult(
            status=ArtifactsPruneStatus.LOCKED,
            dry_run=dry_run,
            force=force,
            errors=[f"Failed to acquire workspace lock: {exc}"],
        )
