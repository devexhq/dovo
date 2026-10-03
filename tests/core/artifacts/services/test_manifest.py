"""Contract tests for artifact manifest checksum and assembly services."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dovo.core.artifacts.models import ArtifactManifestFile
from dovo.core.artifacts.services.manifest import build_manifest, compute_file_checksum


class ManifestServiceTests:
    """Contract tests for compute_file_checksum and build_manifest."""

    def test_compute_file_checksum_matches_known_sha256(self, tmp_path: Path) -> None:
        """[tier-1/unit] compute_file_checksum: a file containing b'hello' returns its known SHA256 hex digest."""
        target = tmp_path / "hello.txt"
        target.write_bytes(b"hello")

        checksum = compute_file_checksum(target)

        assert checksum == hashlib.sha256(b"hello").hexdigest()

    def test_build_manifest_zero_retention_days_yields_null_expires_at(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_manifest: retention_days=0 sets ArtifactManifest.expires_at to None."""
        files = [ArtifactManifestFile(path="dist/pkg.whl", sha256="abc123", size_bytes=10)]

        manifest = build_manifest("dist-packages", "wf_abc123", files, retention_days=0)

        assert manifest.expires_at is None
        assert manifest.size_bytes == 10
        assert manifest.file_count == 1

    def test_build_manifest_positive_retention_days_yields_future_expires_at(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_manifest: retention_days=30 sets expires_at to created_at + 30 days."""
        files = [ArtifactManifestFile(path="dist/pkg.whl", sha256="abc123", size_bytes=10)]

        manifest = build_manifest("dist-packages", "wf_abc123", files, retention_days=30)

        assert manifest.expires_at is not None
        assert manifest.expires_at > manifest.created_at
