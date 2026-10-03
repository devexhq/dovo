"""Publishing service: assemble matched sandbox files into a persisted, checksummed artifact bundle."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from dovo.common.filesystem import Filesystem
from dovo.core.artifacts.models import ArtifactManifestFile, ArtifactUploadResult, ArtifactUploadStatus
from dovo.core.artifacts.services.manifest import build_manifest, compute_file_checksum
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _copy_matching_files_to_temp_dir(
    sandbox_path: Path,
    path_glob: str,
    temp_dir: Path,
) -> list[ArtifactManifestFile]:
    """Copy every regular file matching path_glob under sandbox_path into temp_dir; return their manifest entries."""
    entries: list[ArtifactManifestFile] = []
    for match in sorted(p for p in sandbox_path.glob(path_glob) if p.is_file()):
        rel_path = match.relative_to(sandbox_path)
        dest = temp_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(match, dest)
        entries.append(
            ArtifactManifestFile(path=str(rel_path), sha256=compute_file_checksum(dest), size_bytes=dest.stat().st_size)
        )
    return entries


def _publish_temp_dir_atomically(temp_dir: Path, artifact_dir: Path) -> None:
    """Rename temp_dir into artifact_dir's place via Path.replace, removing any prior artifact_dir first (NFR-1)."""
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.replace(artifact_dir)


def publish_artifact(
    sandbox_path: Path,
    artifacts_dir: Path,
    db: ArtifactsRepository,
    *,
    session_id: str,
    name: str,
    path_glob: str,
    retention_days: int | None,
) -> ArtifactUploadResult:
    """Assemble matching sandbox files via _copy_matching_files_to_temp_dir, checksum them, and publish via _publish_temp_dir_atomically."""
    artifact_dir = artifacts_dir / session_id / name
    temp_dir = artifacts_dir / session_id / f".tmp-{name}-{uuid4().hex}"

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        files = _copy_matching_files_to_temp_dir(sandbox_path, path_glob, temp_dir)
        if not files:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return ArtifactUploadResult(
                status=ArtifactUploadStatus.NO_MATCHING_FILES,
                errors=[f"Artifact path '{path_glob}' did not match any files"],
            )

        manifest = build_manifest(name, session_id, files, retention_days=retention_days)
        Filesystem.atomic_write_json(temp_dir / "manifest.json", manifest.model_dump())
        _publish_temp_dir_atomically(temp_dir, artifact_dir)

        db.create(
            session_id,
            name,
            artifact_dir,
            size_bytes=manifest.size_bytes,
            file_count=manifest.file_count,
            expires_at=manifest.expires_at,
        )

        return ArtifactUploadResult(
            status=ArtifactUploadStatus.OK,
            name=name,
            session_id=session_id,
            size_bytes=manifest.size_bytes,
            file_count=manifest.file_count,
            expires_at=manifest.expires_at,
        )
    except OSError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return ArtifactUploadResult(status=ArtifactUploadStatus.ERROR, errors=[str(exc)])
