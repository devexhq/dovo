"""Contract tests for the prune_artifacts service."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dovo.common.lock import LockTimeoutError
from dovo.core.artifacts.models import ArtifactsPruneStatus, PrunedArtifact
from dovo.core.artifacts.services.prune import prune_artifacts
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _past_timestamp() -> str:
    """Return a timestamp safely in the past for expires_at."""
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


def _create_row(
    repository: ArtifactsRepository,
    path: Path,
    *,
    revision: int = 1,
    digest: str | None = "digest",
    expires_at: str | None,
) -> None:
    """Insert an artifact row for wf_abc123/dist-packages pointing at path."""
    repository.create(
        "wf_abc123",
        "dist-packages",
        path,
        revision=revision,
        size_bytes=1,
        file_count=1,
        expires_at=expires_at,
        digest=digest,
        step_id=None,
        attempt=None,
        loop_iteration=None,
    )


class PruneArtifactsServiceTests:
    """Contract tests for prune_artifacts."""

    def test_prune_artifacts_disabled_without_force_returns_disabled_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        result = prune_artifacts(tmp_path, artifacts_repository, remove_expired=False)

        assert result.status == ArtifactsPruneStatus.DISABLED
        assert result.items == []

    def test_prune_artifacts_lock_timeout_returns_locked_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        with patch(
            "dovo.core.artifacts.services.prune.WorkspaceLock.__enter__",
            side_effect=LockTimeoutError("locked by another process"),
        ):
            result = prune_artifacts(tmp_path, artifacts_repository, remove_expired=True)

        assert result.status == ArtifactsPruneStatus.LOCKED
        assert result.errors == ["Failed to acquire workspace lock: locked by another process"]

    def test_prune_artifacts_deletes_expired_directory_and_db_row(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifact_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
        _create_row(artifacts_repository, artifact_dir, expires_at=_past_timestamp())

        result = prune_artifacts(tmp_path, artifacts_repository, remove_expired=True)

        assert result.status == ArtifactsPruneStatus.OK
        assert result.items == [PrunedArtifact(session_id="wf_abc123", name="dist-packages", revision=1, pruned=True)]
        assert not artifact_dir.exists()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is None
        assert (tmp_path / ".dovo" / ".lock").exists()

    def test_prune_artifacts_dry_run_reports_without_deleting(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifact_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
        _create_row(artifacts_repository, artifact_dir, expires_at=_past_timestamp())

        result = prune_artifacts(tmp_path, artifacts_repository, dry_run=True, remove_expired=True)

        assert result.status == ArtifactsPruneStatus.OK
        assert result.items[0].pruned is True
        assert artifact_dir.exists()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is not None

    def test_prune_artifacts_removes_only_the_expired_revision(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        name_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        for revision in (1, 2):
            (name_dir / str(revision)).mkdir(parents=True)
            (name_dir / str(revision) / "manifest.json").write_text("{}", encoding="utf-8")
        _create_row(artifacts_repository, name_dir / "1", revision=1, expires_at=_past_timestamp())
        _create_row(artifacts_repository, name_dir / "2", revision=2, expires_at=None)

        result = prune_artifacts(tmp_path, artifacts_repository, remove_expired=True)

        assert result.items == [PrunedArtifact(session_id="wf_abc123", name="dist-packages", revision=1, pruned=True)]
        assert not (name_dir / "1").exists()
        assert (name_dir / "2" / "manifest.json").is_file()
        assert [record.revision for record in artifacts_repository.list()] == [2]

    def test_prune_artifacts_legacy_flat_row_keeps_nested_later_revisions(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        name_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        (name_dir / "dist").mkdir(parents=True)
        (name_dir / "dist" / "pkg.whl").write_bytes(b"legacy")
        legacy_manifest = {
            "name": "dist-packages",
            "session_id": "wf_abc123",
            "created_at": "2026-01-01 00:00:00",
            "size_bytes": 6,
            "file_count": 1,
            "files": [{"path": "dist/pkg.whl", "sha256": "x", "size_bytes": 6}],
        }
        (name_dir / "manifest.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")
        (name_dir / "2").mkdir()
        (name_dir / "2" / "manifest.json").write_text("{}", encoding="utf-8")
        _create_row(artifacts_repository, name_dir, revision=1, digest=None, expires_at=_past_timestamp())
        _create_row(artifacts_repository, name_dir / "2", revision=2, expires_at=None)

        result = prune_artifacts(tmp_path, artifacts_repository, remove_expired=True)

        assert result.items == [PrunedArtifact(session_id="wf_abc123", name="dist-packages", revision=1, pruned=True)]
        assert not (name_dir / "manifest.json").exists()
        assert not (name_dir / "dist" / "pkg.whl").exists()
        assert (name_dir / "2" / "manifest.json").is_file()
        assert [record.revision for record in artifacts_repository.list()] == [2]

    def test_prune_artifacts_unreadable_legacy_manifest_reports_failure_and_keeps_row(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        name_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        (name_dir / "2").mkdir(parents=True)
        _create_row(artifacts_repository, name_dir, digest=None, expires_at=_past_timestamp())

        result = prune_artifacts(tmp_path, artifacts_repository, remove_expired=True)

        assert [item.pruned for item in result.items] == [False]
        assert result.items[0].error is not None
        assert (name_dir / "2").is_dir()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is not None
