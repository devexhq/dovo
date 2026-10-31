"""Contract tests for the publish_artifact service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactProvenance, ArtifactUploadStatus
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository

_NO_PROVENANCE = ArtifactProvenance()


def _publish(
    worktree_path: Path,
    artifacts_dir: Path,
    repository: ArtifactsRepository,
    *,
    provenance: ArtifactProvenance = _NO_PROVENANCE,
):
    """Publish dist/*.whl as 'dist-packages' for session wf_abc123."""
    return publish_artifact(
        worktree_path,
        artifacts_dir,
        repository,
        session_id="wf_abc123",
        name="dist-packages",
        path_glob="dist/*.whl",
        retention_days=None,
        provenance=provenance,
    )


def _worktree_with_wheel(tmp_path: Path, content: bytes = b"package-bytes") -> Path:
    """Create a worktree holding dist/pkg.whl with the given bytes."""
    worktree_path = tmp_path / "worktree"
    (worktree_path / "dist").mkdir(parents=True, exist_ok=True)
    (worktree_path / "dist" / "pkg.whl").write_bytes(content)
    return worktree_path


class UploadServiceTests:
    """Contract tests for publish_artifact."""

    def test_publish_artifact_no_matching_files_returns_no_matching_files_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        result = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert result.status == ArtifactUploadStatus.NO_MATCHING_FILES
        assert result.errors == ["Artifact 'dist-packages' matched no files at 'dist/*.whl'."]
        assert not (artifacts_dir / "wf_abc123" / "dist-packages").exists()
        assert artifacts_repository.get("wf_abc123", "dist-packages") is None

    def test_publish_artifact_writes_manifest_and_db_row(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path)
        artifacts_dir = tmp_path / "artifacts"

        result = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert result.status == ArtifactUploadStatus.OK
        assert result.file_count == 1
        assert result.revision == 1

        revision_dir = artifacts_dir / "wf_abc123" / "dist-packages" / "1"
        assert (revision_dir / "dist" / "pkg.whl").read_bytes() == b"package-bytes"

        manifest = json.loads((revision_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["file_count"] == 1
        assert manifest["files"][0]["path"] == "dist/pkg.whl"
        assert manifest["files"][0]["size_bytes"] == len(b"package-bytes")

        record = artifacts_repository.get("wf_abc123", "dist-packages")
        assert record is not None
        assert record.path == revision_dir
        assert record.file_count == 1
        assert record.size_bytes == len(b"package-bytes")

    def test_publish_artifact_copy_failure_returns_error_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path)
        artifacts_dir = tmp_path / "artifacts"

        def _broken_copy2(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("dovo.core.artifacts.services.upload.shutil.copy2", _broken_copy2)

        result = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert result.status == ArtifactUploadStatus.ERROR
        assert "disk full" in result.errors[0]
        assert artifacts_repository.get("wf_abc123", "dist-packages") is None

    def test_publish_artifact_twice_creates_revisions_one_and_two_leaving_first_untouched(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path, b"first")
        artifacts_dir = tmp_path / "artifacts"
        first = _publish(worktree_path, artifacts_dir, artifacts_repository)
        first_dir = artifacts_dir / "wf_abc123" / "dist-packages" / "1"
        first_file_before = (first_dir / "dist" / "pkg.whl").read_bytes()
        first_manifest_before = (first_dir / "manifest.json").read_bytes()

        (worktree_path / "dist" / "pkg.whl").write_bytes(b"second")
        second = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert (first.revision, second.revision) == (1, 2)
        assert (first_dir / "dist" / "pkg.whl").read_bytes() == first_file_before == b"first"
        assert (first_dir / "manifest.json").read_bytes() == first_manifest_before
        second_dir = artifacts_dir / "wf_abc123" / "dist-packages" / "2"
        assert (second_dir / "dist" / "pkg.whl").read_bytes() == b"second"
        assert (second_dir / "manifest.json").is_file()
        assert first.digest != second.digest

    def test_publish_artifact_unchanged_bundle_keeps_the_latest_revision(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path, b"same")
        artifacts_dir = tmp_path / "artifacts"
        first = _publish(worktree_path, artifacts_dir, artifacts_repository)
        manifest_before = (artifacts_dir / "wf_abc123" / "dist-packages" / "1" / "manifest.json").read_bytes()

        again = _publish(worktree_path, artifacts_dir, artifacts_repository, provenance=ArtifactProvenance(attempt=2))

        assert (again.status, again.revision, again.digest, again.file_count) == (
            ArtifactUploadStatus.OK,
            1,
            first.digest,
            1,
        )
        assert [(record.revision, record.attempt) for record in artifacts_repository.list("wf_abc123")] == [(1, None)]
        assert sorted(path.name for path in (artifacts_dir / "wf_abc123" / "dist-packages").iterdir()) == ["1"]
        assert list((artifacts_dir / "wf_abc123").glob(".tmp-*")) == []
        assert (artifacts_dir / "wf_abc123" / "dist-packages" / "1" / "manifest.json").read_bytes() == manifest_before

    def test_publish_artifact_unchanged_bundle_after_expiry_adds_a_revision(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path, b"same")
        artifacts_dir = tmp_path / "artifacts"
        first = _publish(worktree_path, artifacts_dir, artifacts_repository)
        artifacts_repository.delete("wf_abc123", "dist-packages", 1)
        artifacts_repository.create(
            "wf_abc123",
            "dist-packages",
            artifacts_dir / "wf_abc123" / "dist-packages" / "1",
            revision=1,
            size_bytes=4,
            file_count=1,
            expires_at="2000-01-01 00:00:00",
            digest=first.digest,
            step_id=None,
            attempt=None,
            loop_iteration=None,
        )

        again = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert (again.status, again.revision, again.digest) == (ArtifactUploadStatus.OK, 2, first.digest)
        assert sorted(record.revision for record in artifacts_repository.list("wf_abc123")) == [1, 2]

    def test_publish_artifact_lost_revision_race_takes_next_revision(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path)
        artifacts_dir = tmp_path / "artifacts"
        real_next_revision = artifacts_repository.next_revision

        def _next_revision_then_lose_race(session_id: str, name: str) -> int:
            revision = real_next_revision(session_id, name)
            if revision == 1:
                artifacts_repository.create(
                    session_id,
                    name,
                    tmp_path / "competitor",
                    revision=1,
                    size_bytes=0,
                    file_count=0,
                    expires_at=None,
                    digest=None,
                    step_id=None,
                    attempt=None,
                    loop_iteration=None,
                )
            return revision

        monkeypatch.setattr(artifacts_repository, "next_revision", _next_revision_then_lose_race)

        result = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert result.status == ArtifactUploadStatus.OK
        assert result.revision == 2
        assert sorted(record.revision for record in artifacts_repository.list("wf_abc123")) == [1, 2]
        assert (artifacts_dir / "wf_abc123" / "dist-packages" / "2" / "manifest.json").is_file()

    def test_publish_artifact_exhausted_revision_claims_returns_error_status(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path)
        artifacts_dir = tmp_path / "artifacts"
        _publish(worktree_path, artifacts_dir, artifacts_repository)
        (worktree_path / "dist" / "pkg.whl").write_bytes(b"changed")
        monkeypatch.setattr(artifacts_repository, "next_revision", lambda _session_id, _name: 1)

        result = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert result.status == ArtifactUploadStatus.ERROR
        assert result.errors == ["Could not allocate a revision for artifact 'dist-packages'."]
        assert [path.name for path in (artifacts_dir / "wf_abc123").iterdir()] == ["dist-packages"]

    def test_publish_artifact_move_failure_removes_claimed_row(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path)
        artifacts_dir = tmp_path / "artifacts"

        def _broken_rename(_self: Path, _target: Path) -> Path:
            raise OSError("cross-device link")

        monkeypatch.setattr(Path, "rename", _broken_rename)

        result = _publish(worktree_path, artifacts_dir, artifacts_repository)

        assert result.status == ArtifactUploadStatus.ERROR
        assert result.errors == ["cross-device link"]
        assert artifacts_repository.list() == []
        assert list((artifacts_dir / "wf_abc123").glob(".tmp-*")) == []

    def test_publish_artifact_records_digest_and_provenance_in_row_and_manifest(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = _worktree_with_wheel(tmp_path)
        artifacts_dir = tmp_path / "artifacts"
        provenance = ArtifactProvenance(step_id="plan", attempt=3, loop_iteration=2)

        result = _publish(worktree_path, artifacts_dir, artifacts_repository, provenance=provenance)

        manifest = json.loads(
            (artifacts_dir / "wf_abc123" / "dist-packages" / "1" / "manifest.json").read_text(encoding="utf-8")
        )
        record = artifacts_repository.get("wf_abc123", "dist-packages")
        assert record is not None
        assert result.digest is not None
        observed = (
            record.digest,
            record.step_id,
            record.attempt,
            record.loop_iteration,
            record.revision,
        )
        assert observed == (result.digest, "plan", 3, 2, 1)
        assert (
            manifest["digest"],
            manifest["step_id"],
            manifest["attempt"],
            manifest["loop_iteration"],
            manifest["revision"],
        ) == observed
