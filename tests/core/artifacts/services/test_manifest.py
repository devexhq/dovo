"""Contract tests for artifact manifest checksum and assembly services."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dovo.core.artifacts.models import ArtifactManifestFile, ArtifactProvenance
from dovo.core.artifacts.services.manifest import build_manifest, compute_bundle_digest, compute_file_checksum


class ManifestServiceTests:
    """Contract tests for compute_file_checksum and build_manifest."""

    def test_compute_file_checksum_matches_known_sha256(self, tmp_path: Path) -> None:
        target = tmp_path / "hello.txt"
        target.write_bytes(b"hello")

        checksum = compute_file_checksum(target)

        assert checksum == hashlib.sha256(b"hello").hexdigest()

    def test_build_manifest_zero_retention_days_yields_null_expires_at(self, tmp_path: Path) -> None:
        files = [ArtifactManifestFile(path="dist/pkg.whl", sha256="abc123", size_bytes=10)]

        manifest = build_manifest(
            "dist-packages", "wf_abc123", files, revision=1, retention_days=0, provenance=ArtifactProvenance()
        )

        assert manifest.expires_at is None
        assert manifest.size_bytes == 10
        assert manifest.file_count == 1

    def test_build_manifest_positive_retention_days_yields_future_expires_at(self, tmp_path: Path) -> None:
        files = [ArtifactManifestFile(path="dist/pkg.whl", sha256="abc123", size_bytes=10)]

        manifest = build_manifest(
            "dist-packages", "wf_abc123", files, revision=1, retention_days=30, provenance=ArtifactProvenance()
        )

        assert manifest.expires_at is not None
        assert manifest.expires_at > manifest.created_at

    def test_compute_bundle_digest_matches_spec_formula_for_unsorted_input(self) -> None:
        sha_a = hashlib.sha256(b"a").hexdigest()
        sha_b = hashlib.sha256(b"b").hexdigest()
        files = [
            ArtifactManifestFile(path="b.txt", sha256=sha_b, size_bytes=1),
            ArtifactManifestFile(path="a.txt", sha256=sha_a, size_bytes=1),
        ]

        digest = compute_bundle_digest(files)

        assert digest == hashlib.sha256(f"a.txt\0{sha_a}\nb.txt\0{sha_b}\n".encode()).hexdigest()

    def test_build_manifest_carries_revision_digest_and_provenance(self) -> None:
        files = [ArtifactManifestFile(path="a.txt", sha256="abc123", size_bytes=1)]

        manifest = build_manifest(
            "plan",
            "wf_abc123",
            files,
            revision=3,
            retention_days=None,
            provenance=ArtifactProvenance(step_id="plan", attempt=2),
        )

        assert (manifest.revision, manifest.digest, manifest.step_id, manifest.attempt, manifest.loop_iteration) == (
            3,
            compute_bundle_digest(files),
            "plan",
            2,
            None,
        )
