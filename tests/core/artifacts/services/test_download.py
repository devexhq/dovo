"""Contract tests for the download_artifact service."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactDownloadStatus
from dovo.core.artifacts.services.download import download_artifact
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _publish(tmp_path: Path, artifacts_repository: ArtifactsRepository, *, session_id: str, name: str) -> Path:
    """Publish a real artifact bundle for download-service tests and return artifacts_dir."""
    worktree_path = tmp_path / "worktree"
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
    artifacts_dir = tmp_path / "artifacts"
    result = publish_artifact(
        worktree_path,
        artifacts_dir,
        artifacts_repository,
        session_id=session_id,
        name=name,
        path_glob="dist/*.whl",
        retention_days=None,
    )
    assert result.ok
    return artifacts_dir


class DownloadServiceTests:
    """Contract tests for download_artifact."""

    def test_download_artifact_missing_returns_not_found_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] download_artifact: no artifact row for (session_id, name) returns ArtifactDownloadStatus.NOT_FOUND and writes nothing to dest."""
        dest = tmp_path / "out"

        result = download_artifact(
            tmp_path / "artifacts", artifacts_repository, session_id="wf_abc123", name="missing", dest=dest
        )

        assert result.status == ArtifactDownloadStatus.NOT_FOUND
        assert not dest.exists()

    def test_download_artifact_checksum_mismatch_returns_before_any_copy(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] download_artifact: a stored file's live SHA256 no longer matches manifest.json returns ArtifactDownloadStatus.CHECKSUM_MISMATCH and dest remains empty."""
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        (artifacts_dir / "wf_abc123" / "dist-packages" / "dist" / "pkg.whl").write_bytes(b"corrupted")
        dest = tmp_path / "out"

        result = download_artifact(
            artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
        )

        assert result.status == ArtifactDownloadStatus.CHECKSUM_MISMATCH
        assert not dest.exists()

    def test_download_artifact_dest_write_failure_returns_error_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] download_artifact: an OSError raised while writing a verified file into dest returns ArtifactDownloadStatus.ERROR with the OSError message in errors."""
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")

        def _broken_copy2(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("dovo.core.artifacts.services.download.shutil.copy2", _broken_copy2)
        dest = tmp_path / "out"

        result = download_artifact(
            artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
        )

        assert result.status == ArtifactDownloadStatus.ERROR
        assert "disk full" in result.errors[0]

    def test_download_artifact_verified_copies_all_files(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] download_artifact: every file's SHA256 matches manifest.json -> ArtifactDownloadStatus.OK and file_count equals manifest.json's file_count."""
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        dest = tmp_path / "out"

        result = download_artifact(
            artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
        )

        assert result.status == ArtifactDownloadStatus.OK
        assert result.file_count == 1
        assert (dest / "dist" / "pkg.whl").read_bytes() == b"package-bytes"
