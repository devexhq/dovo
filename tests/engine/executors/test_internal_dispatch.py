"""Contract tests for the artifacts.upload internal step command handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.internal_dispatch import handle_artifacts_download, handle_artifacts_upload
from dovo.engine.executors.models import InternalCommandContext


class HandleArtifactsUploadTests:
    """[tier-1/unit] handle_artifacts_upload: env-driven inputs mapped to StepDispatchOutcome."""

    def test_handle_artifacts_upload_success_maps_to_completed_outcome(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] handle_artifacts_upload: ctx.env with matching ARTIFACT_NAME/ARTIFACT_PATH maps a successful publish_artifact() call to StepDispatchOutcome(status='completed')."""
        worktree_path = tmp_path / "worktree"
        (worktree_path / "dist").mkdir(parents=True)
        (worktree_path / "dist" / "pkg.whl").write_bytes(b"bytes")
        artifacts_dir = tmp_path / "artifacts"

        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "dist-packages", "ARTIFACT_PATH": "dist/*.whl"},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_upload(ctx)

        assert outcome.status == "completed"
        assert outcome.exit_code == 0

    def test_handle_artifacts_upload_no_matching_files_maps_to_failed_outcome(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] handle_artifacts_upload: publish_artifact() returning NO_MATCHING_FILES maps to StepDispatchOutcome(status='failed') carrying the same error message."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "dist-packages", "ARTIFACT_PATH": "dist/*.whl"},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_upload(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message is not None
        assert "dist/*.whl" in outcome.error_message

    def test_handle_artifacts_upload_records_context_provenance(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] handle_artifacts_upload: InternalCommandContext(step_id='up', attempt=2, loop_iteration=3) -> the stored row has those values and the same digest as its manifest.json."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        (worktree_path / "PLAN.md").write_text("plan", encoding="utf-8")
        artifacts_dir = tmp_path / "artifacts"
        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_PATH": "PLAN.md"},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
            step_id="up",
            attempt=2,
            loop_iteration=3,
        )

        outcome = handle_artifacts_upload(ctx)

        record = artifacts_repository.get("wf_abc123", "plan")
        assert outcome.status == "completed"
        assert record is not None
        manifest = json.loads((record.path / "manifest.json").read_text(encoding="utf-8"))
        assert (record.step_id, record.attempt, record.loop_iteration) == ("up", 2, 3)
        assert record.digest is not None
        assert record.digest == manifest["digest"]


def _publish_two_revisions(tmp_path: Path, repository: ArtifactsRepository) -> Path:
    """Publish PLAN.md as 'v1' then 'v2' under session wf_abc123 and return artifacts_dir."""
    artifacts_dir = tmp_path / "artifacts"
    worktree_path = tmp_path / "source"
    worktree_path.mkdir()
    for content in ("v1", "v2"):
        (worktree_path / "PLAN.md").write_text(content, encoding="utf-8")
        result = publish_artifact(
            worktree_path,
            artifacts_dir,
            repository,
            session_id="wf_abc123",
            name="plan",
            path_glob="PLAN.md",
            retention_days=None,
            provenance=ArtifactProvenance(),
        )
        assert result.ok
    return artifacts_dir


class HandleArtifactsDownloadTests:
    """[tier-1/integration] handle_artifacts_download: ARTIFACT_REVISION handling."""

    def test_handle_artifacts_download_artifact_revision_extracts_that_revision(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] handle_artifacts_download: ARTIFACT_REVISION='1' -> completed outcome and revision 1's bytes in ARTIFACT_DEST."""
        artifacts_dir = _publish_two_revisions(tmp_path, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": "1"},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "completed"
        assert (tmp_path / "out" / "PLAN.md").read_text(encoding="utf-8") == "v1"

    def test_handle_artifacts_download_without_revision_extracts_latest(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] handle_artifacts_download: unset (or empty) ARTIFACT_REVISION -> the latest revision's bytes in ARTIFACT_DEST."""
        artifacts_dir = _publish_two_revisions(tmp_path, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": ""},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "completed"
        assert (tmp_path / "out" / "PLAN.md").read_text(encoding="utf-8") == "v2"

    @pytest.mark.parametrize("raw", ["abc", "0", "-2"])
    def test_handle_artifacts_download_invalid_revision_fails_without_download(
        self, raw: str, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] handle_artifacts_download: invalid ARTIFACT_REVISION -> failed, exit_code 1, the positive-integer message, and nothing downloaded."""
        artifacts_dir = _publish_two_revisions(tmp_path, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": raw},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_download(ctx)

        assert (outcome.status, outcome.exit_code) == ("failed", 1)
        assert outcome.error_message == (
            "Internal command 'artifacts.download' requires ARTIFACT_REVISION to be a positive integer."
        )
        assert not (tmp_path / "out").exists()

    def test_handle_artifacts_download_unknown_revision_fails_with_not_found_text(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] handle_artifacts_download: ARTIFACT_REVISION='9' with no such revision -> failed with the revision-specific not-found text."""
        artifacts_dir = _publish_two_revisions(tmp_path, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": "9"},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message == "Artifact 'plan' revision 9 not found for session 'wf_abc123'"
