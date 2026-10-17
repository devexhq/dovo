"""Contract tests for the artifacts.upload internal step command handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.internal_dispatch import handle_artifacts_download, handle_artifacts_upload
from dovo.engine.executors.models import InternalCommandContext
from tests.conftest import PublishArtifact


class HandleArtifactsUploadTests:
    def test_handle_artifacts_upload_no_matching_files_maps_to_failed_outcome(
        self, tmp_path: Path, artifact_store: ArtifactStore
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            env={"ARTIFACT_NAME": "dist-packages", "ARTIFACT_PATH": "dist/*.whl"},
            artifact_store=artifact_store,
        )

        outcome = handle_artifacts_upload(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message is not None
        assert "dist/*.whl" in outcome.error_message

    def test_handle_artifacts_upload_records_context_provenance(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, artifact_store: ArtifactStore
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        (worktree_path / "PLAN.md").write_text("plan", encoding="utf-8")
        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_PATH": "PLAN.md"},
            artifact_store=artifact_store,
            step_id="up",
            attempt=2,
            loop_iteration=3,
        )

        outcome = handle_artifacts_upload(ctx)

        record = artifacts_repository.get("wf_1", "plan")
        assert (outcome.status, outcome.exit_code) == ("completed", 0)
        assert record is not None
        manifest = json.loads((record.path / "manifest.json").read_text(encoding="utf-8"))
        assert (record.step_id, record.attempt, record.loop_iteration) == ("up", 2, 3)
        assert record.digest is not None
        assert record.digest == manifest["digest"]


def _publish_two_revisions(publish_artifact: PublishArtifact) -> None:
    """Publish PLAN.md as 'v1' then 'v2'."""
    publish_artifact("plan", {"PLAN.md": "v1"})
    publish_artifact("plan", {"PLAN.md": "v2"})


class HandleArtifactsDownloadTests:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [pytest.param("1", "v1", id="artifact_revision"), pytest.param("", "v2", id="latest_without_revision")],
    )
    def test_handle_artifacts_download_extracts_requested_or_latest_revision(
        self, raw: str, expected: str, tmp_path: Path, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        _publish_two_revisions(publish_artifact)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": raw},
            artifact_store=artifact_store,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "completed"
        assert (tmp_path / "out" / "PLAN.md").read_text(encoding="utf-8") == expected

    @pytest.mark.parametrize("raw", ["abc", "0", "-2"])
    def test_handle_artifacts_download_invalid_revision_fails_without_download(
        self, raw: str, tmp_path: Path, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        _publish_two_revisions(publish_artifact)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": raw},
            artifact_store=artifact_store,
        )

        outcome = handle_artifacts_download(ctx)

        assert (outcome.status, outcome.exit_code) == ("failed", 1)
        assert outcome.error_message == (
            "Internal command 'artifacts.download' requires ARTIFACT_REVISION to be a positive integer."
        )
        assert not (tmp_path / "out").exists()

    def test_handle_artifacts_download_unknown_revision_fails_with_not_found_text(
        self, tmp_path: Path, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        _publish_two_revisions(publish_artifact)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": "9"},
            artifact_store=artifact_store,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message == "Artifact 'plan' revision 9 not found for session 'wf_1'"
