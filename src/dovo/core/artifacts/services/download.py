"""Download service: checksum-verify a published artifact bundle before extracting it."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.core.artifacts.exceptions import ArtifactError
from dovo.core.artifacts.models import ArtifactDownloadResult, ArtifactManifest
from dovo.core.artifacts.services.manifest import compute_file_checksum
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _verify_manifest_checksums(artifact_dir: Path, manifest: ArtifactManifest) -> list[str]:
    """Return the relative paths whose live SHA256 no longer matches manifest.json, without copying anything.

    Raises:
        ArtifactError: ARTIFACT_DOWNLOAD_FAILED when a stored file cannot be read.
    """
    mismatches: list[str] = []
    try:
        for entry in manifest.files:
            file_path = artifact_dir / entry.path
            if not file_path.is_file() or compute_file_checksum(file_path) != entry.sha256:
                mismatches.append(entry.path)
    except OSError as error:
        raise ArtifactError(ErrorCode.ARTIFACT_DOWNLOAD_FAILED, str(error)) from error
    return mismatches


def _extract_verified_files(artifact_dir: Path, manifest: ArtifactManifest, dest: Path) -> None:
    """Copy every manifest-listed file from artifact_dir into dest; caller guarantees checksums already verified."""
    for entry in manifest.files:
        target = dest / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact_dir / entry.path, target)


def _stored_cause(error: OSError, artifact_dir: Path) -> OSError | None:
    """Return error when it hit a file under artifact_dir (Dovo's own storage), else None for a destination failure."""
    filename = error.filename
    if isinstance(filename, str | Path) and Path(filename).is_relative_to(artifact_dir):
        return error
    return None


def _read_manifest(artifact_dir: Path) -> ArtifactManifest:
    """Read artifact_dir's manifest.json, raising ArtifactError ARTIFACT_DOWNLOAD_FAILED when it cannot be read."""
    try:
        return ArtifactManifest.model_validate_json((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    except OSError as error:
        raise ArtifactError(ErrorCode.ARTIFACT_DOWNLOAD_FAILED, str(error)) from error


def download_artifact(
    artifacts_dir: Path,
    db: ArtifactsRepository,
    *,
    session_id: str,
    name: str,
    dest: Path,
    revision: int | None = None,
) -> ArtifactDownloadResult:
    """Look up the named revision (latest by default), verify every file's checksum, then extract.

    Raises:
        ArtifactError: ARTIFACT_NOT_FOUND, ARTIFACT_CHECKSUM_INVALID (dest untouched) or ARTIFACT_DOWNLOAD_FAILED.
    """
    record = db.get(session_id, name, revision)
    if record is None:
        missing = (
            f"Artifact '{name}' not found for session '{session_id}'"
            if revision is None
            else f"Artifact '{name}' revision {revision} not found for session '{session_id}'"
        )
        raise ArtifactError(ErrorCode.ARTIFACT_NOT_FOUND, missing)

    artifact_dir = Path(record.path)
    manifest = _read_manifest(artifact_dir)

    mismatches = _verify_manifest_checksums(artifact_dir, manifest)
    if mismatches:
        raise ArtifactError(ErrorCode.ARTIFACT_CHECKSUM_INVALID, f"Checksum mismatch for: {', '.join(mismatches)}")

    try:
        _extract_verified_files(artifact_dir, manifest, dest)
    except OSError as error:
        raise ArtifactError(ErrorCode.ARTIFACT_DOWNLOAD_FAILED, str(error)) from _stored_cause(error, artifact_dir)

    return ArtifactDownloadResult(
        session_id=session_id,
        name=name,
        dest=str(dest),
        revision=record.revision,
        file_count=manifest.file_count,
    )
