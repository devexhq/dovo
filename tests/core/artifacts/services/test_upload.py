"""Contract tests for the publish_artifact service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactUploadStatus
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository


class UploadServiceTests:
    """Contract tests for publish_artifact."""

    def test_publish_artifact_no_matching_files_returns_no_matching_files_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] publish_artifact: glob matching zero files returns ArtifactUploadStatus.NO_MATCHING_FILES and creates no directory or DB row."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        result = publish_artifact(
            worktree_path,
            artifacts_dir,
            artifacts_repository,
            session_id="wf_abc123",
            name="dist-packages",
            path_glob="dist/*.whl",
            retention_days=None,
        )

        assert result.status == ArtifactUploadStatus.NO_MATCHING_FILES
        assert "dist/*.whl" in result.errors[0]
        assert not (artifacts_dir / "wf_abc123" / "dist-packages").exists()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is None

    def test_publish_artifact_writes_manifest_and_db_row(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] publish_artifact: matching files under worktree_path are copied to artifacts_dir/<session_id>/<name>/, manifest.json lists each file's exact SHA256 and size, and an ArtifactRecord row is created."""
        worktree_path = tmp_path / "worktree"
        (worktree_path / "dist").mkdir(parents=True)
        (worktree_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
        artifacts_dir = tmp_path / "artifacts"

        result = publish_artifact(
            worktree_path,
            artifacts_dir,
            artifacts_repository,
            session_id="wf_abc123",
            name="dist-packages",
            path_glob="dist/*.whl",
            retention_days=None,
        )

        assert result.status == ArtifactUploadStatus.OK
        assert result.file_count == 1

        artifact_dir = artifacts_dir / "wf_abc123" / "dist-packages"
        published_file = artifact_dir / "dist" / "pkg.whl"
        assert published_file.read_bytes() == b"package-bytes"

        manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["file_count"] == 1
        assert manifest["files"][0]["path"] == "dist/pkg.whl"
        assert manifest["files"][0]["size_bytes"] == len(b"package-bytes")

        record = artifacts_repository.get("wf_abc123", "dist-packages")
        assert record is not None
        assert record.file_count == 1
        assert record.size_bytes == len(b"package-bytes")

    def test_publish_artifact_copy_failure_returns_error_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] publish_artifact: an OSError raised while copying a matched file returns ArtifactUploadStatus.ERROR with the OSError message in errors, and creates no DB row."""
        worktree_path = tmp_path / "worktree"
        (worktree_path / "dist").mkdir(parents=True)
        (worktree_path / "dist" / "pkg.whl").write_bytes(b"package-bytes")
        artifacts_dir = tmp_path / "artifacts"

        def _broken_copy2(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("dovo.core.artifacts.services.upload.shutil.copy2", _broken_copy2)

        result = publish_artifact(
            worktree_path,
            artifacts_dir,
            artifacts_repository,
            session_id="wf_abc123",
            name="dist-packages",
            path_glob="dist/*.whl",
            retention_days=None,
        )

        assert result.status == ArtifactUploadStatus.ERROR
        assert "disk full" in result.errors[0]
        assert artifacts_repository.get("wf_abc123", "dist-packages") is None
