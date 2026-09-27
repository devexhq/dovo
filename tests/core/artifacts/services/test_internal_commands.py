"""Contract tests for the artifacts.upload internal step command handler."""

from __future__ import annotations

from pathlib import Path

from worktree.core.artifacts.services.internal_commands import handle_artifacts_upload
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.step.models import InternalCommandContext


class HandleArtifactsUploadTests:
    """[tier-1/unit] handle_artifacts_upload: env-driven inputs mapped to StepDispatchOutcome."""

    def test_handle_artifacts_upload_success_maps_to_completed_outcome(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] handle_artifacts_upload: ctx.env with matching ARTIFACT_NAME/ARTIFACT_PATH maps a successful publish_artifact() call to StepDispatchOutcome(status='completed')."""
        sandbox_path = tmp_path / "sandbox"
        (sandbox_path / "dist").mkdir(parents=True)
        (sandbox_path / "dist" / "pkg.whl").write_bytes(b"bytes")
        artifacts_dir = tmp_path / "artifacts"

        ctx = InternalCommandContext(
            sandbox_path=sandbox_path,
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
        sandbox_path = tmp_path / "sandbox"
        sandbox_path.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        ctx = InternalCommandContext(
            sandbox_path=sandbox_path,
            session_id="wf_abc123",
            env={"ARTIFACT_NAME": "dist-packages", "ARTIFACT_PATH": "dist/*.whl"},
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_repository,
        )

        outcome = handle_artifacts_upload(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message is not None
        assert "dist/*.whl" in outcome.error_message
