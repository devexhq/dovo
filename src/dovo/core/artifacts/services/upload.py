"""Publishing service: assemble matched worktree files into an immutable, checksummed artifact revision."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from dovo.common.filesystem import Filesystem
from dovo.core.artifacts.models import (
    ArtifactManifest,
    ArtifactManifestFile,
    ArtifactProvenance,
    ArtifactUploadResult,
    ArtifactUploadStatus,
)
from dovo.core.artifacts.services.manifest import build_manifest, compute_file_checksum
from dovo.core.db.repositories.artifacts import ArtifactsRepository

_MAX_REVISION_CLAIMS = 5


def _stage_bundle(worktree_path: Path, path_glob: str, temp_dir: Path) -> list[ArtifactManifestFile]:
    """Copy every regular file matching path_glob under worktree_path into temp_dir; return their manifest entries."""
    entries: list[ArtifactManifestFile] = []
    for match in sorted(p for p in worktree_path.glob(path_glob) if p.is_file()):
        rel_path = match.relative_to(worktree_path)
        dest = temp_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(match, dest)
        entries.append(
            ArtifactManifestFile(path=str(rel_path), sha256=compute_file_checksum(dest), size_bytes=dest.stat().st_size)
        )
    return entries


def _claim_revision(
    db: ArtifactsRepository,
    manifest: ArtifactManifest,
    name_dir: Path,
) -> ArtifactManifest | None:
    """Insert the row for manifest.revision, retrying on the next free revision; None when claims are exhausted."""
    claimed = manifest
    for _ in range(_MAX_REVISION_CLAIMS):
        try:
            db.create(
                claimed.session_id,
                claimed.name,
                name_dir / str(claimed.revision),
                revision=claimed.revision,
                size_bytes=claimed.size_bytes,
                file_count=claimed.file_count,
                expires_at=claimed.expires_at,
                digest=claimed.digest,
                step_id=claimed.step_id,
                attempt=claimed.attempt,
                loop_iteration=claimed.loop_iteration,
            )
        except ValueError:
            claimed = claimed.model_copy(update={"revision": db.next_revision(claimed.session_id, claimed.name)})
            continue
        return claimed
    return None


def _move_into_revision_directory(temp_dir: Path, revision_dir: Path) -> None:
    """Rename temp_dir to revision_dir, refusing to replace an existing directory."""
    if revision_dir.exists():
        raise FileExistsError(f"Artifact revision directory already exists: {revision_dir}")
    revision_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.rename(revision_dir)


def _write_manifest_and_move(
    db: ArtifactsRepository,
    manifest: ArtifactManifest,
    temp_dir: Path,
    name_dir: Path,
) -> ArtifactUploadResult:
    """Write manifest.json and move the bundle into place; the claimed row is released on failure."""
    try:
        Filesystem.atomic_write_json(temp_dir / "manifest.json", manifest.model_dump())
        _move_into_revision_directory(temp_dir, name_dir / str(manifest.revision))
    except OSError as exc:
        db.delete(manifest.session_id, manifest.name, manifest.revision)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return ArtifactUploadResult(status=ArtifactUploadStatus.ERROR, errors=[str(exc)])

    return ArtifactUploadResult(
        status=ArtifactUploadStatus.OK,
        name=manifest.name,
        session_id=manifest.session_id,
        revision=manifest.revision,
        digest=manifest.digest,
        size_bytes=manifest.size_bytes,
        file_count=manifest.file_count,
        expires_at=manifest.expires_at,
    )


def _unchanged_latest(db: ArtifactsRepository, manifest: ArtifactManifest) -> ArtifactUploadResult | None:
    """Return the latest revision as the publish's result when it holds the same bundle digest and has not expired."""
    latest = db.get(manifest.session_id, manifest.name)
    if latest is None or latest.digest != manifest.digest:
        return None
    if latest.expires_at is not None and latest.expires_at <= manifest.created_at:
        return None
    return ArtifactUploadResult(
        status=ArtifactUploadStatus.OK,
        name=latest.name,
        session_id=latest.session_id,
        revision=latest.revision,
        digest=latest.digest,
        size_bytes=latest.size_bytes,
        file_count=latest.file_count,
        expires_at=latest.expires_at,
    )


def publish_artifact(
    worktree_path: Path,
    artifacts_dir: Path,
    db: ArtifactsRepository,
    *,
    session_id: str,
    name: str,
    path_glob: str,
    retention_days: int | None,
    provenance: ArtifactProvenance,
) -> ArtifactUploadResult:
    """Stage matching files, claim the next revision, write manifest.json, and move the bundle into <name>/<revision>/.

    A bundle whose digest equals the latest unexpired revision's adds no revision: the result names that revision.
    """
    name_dir = artifacts_dir / session_id / name
    temp_dir = artifacts_dir / session_id / f".tmp-{name}-{uuid4().hex}"

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        files = _stage_bundle(worktree_path, path_glob, temp_dir)
        if not files:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return ArtifactUploadResult(
                status=ArtifactUploadStatus.NO_MATCHING_FILES,
                errors=[f"Artifact '{name}' matched no files at '{path_glob}'."],
            )

        manifest = build_manifest(
            name,
            session_id,
            files,
            revision=db.next_revision(session_id, name),
            retention_days=retention_days,
            provenance=provenance,
        )
        kept = _unchanged_latest(db, manifest)
        if kept is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return kept
        claimed = _claim_revision(db, manifest, name_dir)
        if claimed is None:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return ArtifactUploadResult(
                status=ArtifactUploadStatus.ERROR,
                errors=[f"Could not allocate a revision for artifact '{name}'."],
            )

        return _write_manifest_and_move(db, claimed, temp_dir, name_dir)
    except OSError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return ArtifactUploadResult(status=ArtifactUploadStatus.ERROR, errors=[str(exc)])
