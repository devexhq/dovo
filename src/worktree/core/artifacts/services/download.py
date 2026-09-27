"""Download service: checksum-verify a published artifact bundle before extracting it."""

from __future__ import annotations

import shutil
from pathlib import Path

from worktree.core.artifacts.models import ArtifactDownloadResult, ArtifactDownloadStatus, ArtifactManifest
from worktree.core.artifacts.services.manifest import compute_file_checksum
from worktree.core.db.repositories.artifacts import ArtifactsRepository


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
) -> ArtifactDownloadResult:
    """Look up the artifact, verify every file's checksum via _verify_manifest_checksums, then extract via _extract_verified_files."""
    record = db.get(session_id, name)
    if record is None:
        return ArtifactDownloadResult(
            status=ArtifactDownloadStatus.NOT_FOUND,
            session_id=session_id,
            name=name,
            errors=[f"Artifact '{name}' not found for session '{session_id}'"],
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
        file_count=manifest.file_count,
    )
