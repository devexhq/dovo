"""Contract tests for the prune_artifacts service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from worktree.common.lock import LockTimeoutError
from worktree.core.artifacts.models import ArtifactsPruneStatus
from worktree.core.artifacts.services.prune import prune_artifacts
from worktree.core.db.repositories.artifacts import ArtifactsRepository


def _past_timestamp() -> str:
    """Return a timestamp safely in the past for expires_at."""
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


class PruneArtifactsServiceTests:
    """Contract tests for prune_artifacts."""

    def test_prune_artifacts_disabled_without_force_returns_disabled_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] prune_artifacts: remove_expired=False and force=False returns ArtifactsPruneStatus.DISABLED with an empty items list, and deletes nothing."""
        result = prune_artifacts(tmp_path, tmp_path / "artifacts", artifacts_repository, remove_expired=False)

        assert result.status == ArtifactsPruneStatus.DISABLED
        assert result.items == []

    def test_prune_artifacts_lock_timeout_returns_locked_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] prune_artifacts: WorkspaceLock.__enter__ raising LockTimeoutError returns ArtifactsPruneStatus.LOCKED without raising."""
        with patch(
            "worktree.core.artifacts.services.prune.WorkspaceLock.__enter__",
            side_effect=LockTimeoutError("locked by another process"),
        ):
            result = prune_artifacts(tmp_path, tmp_path / "artifacts", artifacts_repository, remove_expired=True)

        assert result.status == ArtifactsPruneStatus.LOCKED
        assert result.errors == ["Failed to acquire workspace lock: locked by another process"]

    def test_prune_artifacts_deletes_expired_directory_and_db_row(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] prune_artifacts: an artifact whose expires_at is in the past is removed from disk and from ArtifactsRepository, and reported as PrunedArtifact(pruned=True)."""
        artifact_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
        artifacts_repository.create(
            "wf_abc123", "dist-packages", artifact_dir, size_bytes=1, file_count=1, expires_at=_past_timestamp()
        )

        result = prune_artifacts(tmp_path, tmp_path / "artifacts", artifacts_repository, remove_expired=True)

        assert result.status == ArtifactsPruneStatus.OK
        assert len(result.items) == 1
        assert result.items[0].session_id == "wf_abc123"
        assert result.items[0].name == "dist-packages"
        assert result.items[0].pruned is True
        assert not artifact_dir.exists()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is None

    def test_prune_artifacts_dry_run_reports_without_deleting(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] prune_artifacts: dry_run=True reports the same expired artifact as PrunedArtifact(pruned=True) but leaves its directory and DB row untouched."""
        artifact_dir = tmp_path / "artifacts" / "wf_abc123" / "dist-packages"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
        artifacts_repository.create(
            "wf_abc123", "dist-packages", artifact_dir, size_bytes=1, file_count=1, expires_at=_past_timestamp()
        )

        result = prune_artifacts(
            tmp_path, tmp_path / "artifacts", artifacts_repository, dry_run=True, remove_expired=True
        )

        assert result.status == ArtifactsPruneStatus.OK
        assert result.items[0].pruned is True
        assert artifact_dir.exists()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is not None

    def test_prune_artifacts_locks_workspace_root_not_artifacts_dir(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] prune_artifacts: the advisory lock file lands under workspace_root/.worktree/.lock, not under artifacts_dir, so it coordinates with every other WorkspaceLock caller."""
        artifacts_dir = tmp_path / "artifacts"
        artifact_dir = artifacts_dir / "wf_abc123" / "dist-packages"
        artifact_dir.mkdir(parents=True)
        artifacts_repository.create(
            "wf_abc123", "dist-packages", artifact_dir, size_bytes=1, file_count=1, expires_at=_past_timestamp()
        )

        prune_artifacts(tmp_path, artifacts_dir, artifacts_repository, remove_expired=True)

        assert (tmp_path / ".worktree" / ".lock").exists()
        assert not (artifacts_dir / ".worktree").exists()
