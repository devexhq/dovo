"""Contract tests for the download_artifact service."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.core.artifacts.exceptions import ArtifactError
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.services.download import download_artifact
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _publish(
    tmp_path: Path,
    artifacts_repository: ArtifactsRepository,
    *,
    session_id: str,
    name: str,
    content: bytes = b"package-bytes",
) -> Path:
    """Publish a real artifact bundle for download-service tests and return artifacts_dir."""
    worktree_path = tmp_path / "worktree"
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(content)
    artifacts_dir = tmp_path / "artifacts"
    result = publish_artifact(
        worktree_path,
        artifacts_dir,
        artifacts_repository,
        session_id=session_id,
        name=name,
        path_glob="dist/*.whl",
        retention_days=None,
        provenance=ArtifactProvenance(),
    )
    assert result.ok
    return artifacts_dir


class DownloadServiceTests:
    """Contract tests for download_artifact."""

    def test_download_artifact_missing_raises_not_found(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        dest = tmp_path / "out"

        with pytest.raises(ArtifactError) as raised:
            download_artifact(
                tmp_path / "artifacts", artifacts_repository, session_id="wf_abc123", name="missing", dest=dest
            )

        assert (raised.value.error_code, raised.value.message) == (
            ErrorCode.ARTIFACT_NOT_FOUND,
            "Artifact 'missing' not found for session 'wf_abc123'",
        )
        assert not dest.exists()

    def test_download_artifact_checksum_mismatch_raises_before_any_copy(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        (artifacts_dir / "wf_abc123" / "dist-packages" / "1" / "dist" / "pkg.whl").write_bytes(b"corrupted")
        dest = tmp_path / "out"

        with pytest.raises(ArtifactError) as raised:
            download_artifact(
                artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
            )

        assert (raised.value.error_code, raised.value.message) == (
            ErrorCode.ARTIFACT_CHECKSUM_INVALID,
            "Checksum mismatch for: dist/pkg.whl",
        )
        assert not dest.exists()

    def test_download_artifact_dest_write_failure_raises_download_failed_without_cause(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        dest = tmp_path / "out"

        def _broken_copy2(*_args: object, **_kwargs: object) -> None:
            raise OSError(28, "No space left on device", str(dest / "dist" / "pkg.whl"))

        monkeypatch.setattr("dovo.core.artifacts.services.download.shutil.copy2", _broken_copy2)

        with pytest.raises(ArtifactError) as raised:
            download_artifact(
                artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
            )

        assert (raised.value.error_code, raised.value.message, raised.value.__cause__) == (
            ErrorCode.ARTIFACT_DOWNLOAD_FAILED,
            f"[Errno 28] No space left on device: '{dest / 'dist' / 'pkg.whl'}'",
            None,
        )

    def test_download_artifact_stored_file_read_failure_chains_its_os_error(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        stored = artifacts_dir / "wf_abc123" / "dist-packages" / "1" / "dist" / "pkg.whl"
        read_error = PermissionError(13, "Permission denied", str(stored))

        def _broken_copy2(*_args: object, **_kwargs: object) -> None:
            raise read_error

        monkeypatch.setattr("dovo.core.artifacts.services.download.shutil.copy2", _broken_copy2)

        with pytest.raises(ArtifactError) as raised:
            download_artifact(
                artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=tmp_path / "out"
            )

        assert (raised.value.error_code, raised.value.__cause__) == (ErrorCode.ARTIFACT_DOWNLOAD_FAILED, read_error)

    def test_download_artifact_stored_file_checksum_read_failure_raises_download_failed_before_any_copy(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        stored = artifacts_dir / "wf_abc123" / "dist-packages" / "1" / "dist" / "pkg.whl"
        read_error = PermissionError(13, "Permission denied", str(stored))
        dest = tmp_path / "out"

        def _broken_checksum(_path: Path) -> str:
            raise read_error

        monkeypatch.setattr("dovo.core.artifacts.services.download.compute_file_checksum", _broken_checksum)

        with pytest.raises(ArtifactError) as raised:
            download_artifact(
                artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
            )

        assert (raised.value.error_code, raised.value.message, raised.value.__cause__) == (
            ErrorCode.ARTIFACT_DOWNLOAD_FAILED,
            f"[Errno 13] Permission denied: '{stored}'",
            read_error,
        )
        assert not dest.exists()

    def test_download_artifact_verified_copies_all_files(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        dest = tmp_path / "out"

        result = download_artifact(
            artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=dest
        )

        assert result.ok
        assert result.file_count == 1
        assert result.revision == 1
        assert (dest / "dist" / "pkg.whl").read_bytes() == b"package-bytes"

    def test_download_artifact_revision_selects_that_revisions_files(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages", content=b"v1")
        artifacts_dir = _publish(
            tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages", content=b"v2"
        )
        first_dest = tmp_path / "first"
        latest_dest = tmp_path / "latest"

        first = download_artifact(
            artifacts_dir,
            artifacts_repository,
            session_id="wf_abc123",
            name="dist-packages",
            dest=first_dest,
            revision=1,
        )
        latest = download_artifact(
            artifacts_dir, artifacts_repository, session_id="wf_abc123", name="dist-packages", dest=latest_dest
        )

        assert (first.revision, (first_dest / "dist" / "pkg.whl").read_bytes()) == (1, b"v1")
        assert (latest.revision, (latest_dest / "dist" / "pkg.whl").read_bytes()) == (2, b"v2")

    def test_download_artifact_unknown_revision_raises_not_found_message(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts_dir = _publish(tmp_path, artifacts_repository, session_id="wf_abc123", name="dist-packages")
        dest = tmp_path / "out"

        with pytest.raises(ArtifactError) as raised:
            download_artifact(
                artifacts_dir,
                artifacts_repository,
                session_id="wf_abc123",
                name="dist-packages",
                dest=dest,
                revision=9,
            )

        assert (raised.value.error_code, raised.value.message) == (
            ErrorCode.ARTIFACT_NOT_FOUND,
            "Artifact 'dist-packages' revision 9 not found for session 'wf_abc123'",
        )
        assert not dest.exists()
