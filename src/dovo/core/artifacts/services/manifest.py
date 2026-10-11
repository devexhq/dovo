"""Manifest assembly and checksum services for published artifact bundles."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dovo.core.artifacts.models import ArtifactManifest, ArtifactManifestFile, ArtifactProvenance

_CHECKSUM_CHUNK_SIZE = 65536


def compute_file_checksum(path: Path) -> str:
    """Compute the SHA256 hex digest of a file's raw bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHECKSUM_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_expires_at(created_at: datetime, retention_days: int | None) -> str | None:
    """Resolve expires_at: None/0 retention never expires; a positive int adds that many days to created_at."""
    if not retention_days:
        return None
    return (created_at + timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")


def compute_bundle_digest(files: list[ArtifactManifestFile]) -> str:
    """Return the sha256 hex of a "path NUL sha256 newline" line per file, sorted by relative path."""
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda file: file.path):
        digest.update(f"{entry.path}\0{entry.sha256}\n".encode())
    return digest.hexdigest()


def build_manifest(
    name: str,
    session_id: str,
    files: list[ArtifactManifestFile],
    *,
    revision: int,
    retention_days: int | None,
    provenance: ArtifactProvenance,
) -> ArtifactManifest:
    """Assemble an ArtifactManifest, including its bundle digest and provenance, from checksummed files."""
    created_at = datetime.now(UTC)

    return ArtifactManifest(
        name=name,
        session_id=session_id,
        revision=revision,
        digest=compute_bundle_digest(files),
        step_id=provenance.step_id,
        attempt=provenance.attempt,
        loop_iteration=provenance.loop_iteration,
        created_at=created_at.strftime("%Y-%m-%d %H:%M:%S"),
        expires_at=_resolve_expires_at(created_at, retention_days),
        size_bytes=sum(f.size_bytes for f in files),
        file_count=len(files),
        files=files,
    )
