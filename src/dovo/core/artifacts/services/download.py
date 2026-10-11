"""Download service: checksum-verify a published artifact bundle before extracting it."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.core.artifacts.models import ArtifactDownloadResult, ArtifactDownloadStatus, ArtifactManifest
from dovo.core.artifacts.services.manifest import compute_file_checksum
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _verify_manifest_checksums(artifact_dir: Path, manifest: ArtifactManifest) -> list[str]:
    """Return the relative paths whose live SHA256 no longer matches manifest.json, without copying anything."""
    mismatches: list[str] = []
    for entry in manifest.files:
        file_path = artifact_dir / entry.path
        if not file_path.is_file() or compute_file_checksum(file_path) != entry.sha256:
            mismatches.append(entry.path)
    return mismatches


def _extract_verified_files(artifact_dir: Path, manifest: ArtifactManifest, dest: Path) -> None:
    """Copy every manifest-listed file from artifact_dir into dest; caller guarantees checksums already verified."""
    for entry in manifest.files:
        target = dest / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact_dir / entry.path, target)


def download_artifact(
    artifacts_dir: Path,
    db: ArtifactsRepository,
    *,
    session_id: str,
    name: str,
    dest: Path,
    revision: int | None = None,
) -> ArtifactDownloadResult:
    """Look up the named revision (latest by default), verify every file's checksum, then extract."""
    record = db.get(session_id, name, revision)
    if record is None:
        missing = (
            f"Artifact '{name}' not found for session '{session_id}'"
            if revision is None
            else f"Artifact '{name}' revision {revision} not found for session '{session_id}'"
        )
        return ArtifactDownloadResult(
            status=ArtifactDownloadStatus.NOT_FOUND, session_id=session_id, name=name, errors=[missing]
        )

    artifact_dir = Path(record.path)
    try:
        manifest = ArtifactManifest.model_validate_json((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    except OSError as exc:
        return ArtifactDownloadResult(
            status=ArtifactDownloadStatus.ERROR, session_id=session_id, name=name, errors=[str(exc)]
        )

    mismatches = _verify_manifest_checksums(artifact_dir, manifest)
    if mismatches:
        return ArtifactDownloadResult(
            status=ArtifactDownloadStatus.CHECKSUM_MISMATCH,
            session_id=session_id,
            name=name,
            errors=[f"Checksum mismatch for: {', '.join(mismatches)}"],
        )

    try:
        _extract_verified_files(artifact_dir, manifest, dest)
    except OSError as exc:
        return ArtifactDownloadResult(
            status=ArtifactDownloadStatus.ERROR, session_id=session_id, name=name, errors=[str(exc)]
        )

    return ArtifactDownloadResult(
        status=ArtifactDownloadStatus.OK,
        session_id=session_id,
        name=name,
        dest=str(dest),
        revision=record.revision,
        file_count=manifest.file_count,
    )
