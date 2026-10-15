"""Contract tests for the artifacts.upload internal step command handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.internal_dispatch import handle_artifacts_download, handle_artifacts_upload
from dovo.engine.executors.models import InternalCommandContext


class HandleArtifactsUploadTests:
    def test_handle_artifacts_upload_success_maps_to_completed_outcome(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = tmp_path / "worktree"
        (worktree_path / "dist").mkdir(parents=True)
        (worktree_path / "dist" / "pkg.whl").write_bytes(b"bytes")

        store = _store(engine_paths, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            env={"ARTIFACT_NAME": "dist-packages", "ARTIFACT_PATH": "dist/*.whl"},
            artifact_store=store,
        )

        outcome = handle_artifacts_upload(ctx)

        assert outcome.status == "completed"
        assert outcome.exit_code == 0

    def test_handle_artifacts_upload_no_matching_files_maps_to_failed_outcome(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        store = _store(engine_paths, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            env={"ARTIFACT_NAME": "dist-packages", "ARTIFACT_PATH": "dist/*.whl"},
            artifact_store=store,
        )

        outcome = handle_artifacts_upload(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message is not None
        assert "dist/*.whl" in outcome.error_message

    def test_handle_artifacts_upload_records_context_provenance(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        (worktree_path / "PLAN.md").write_text("plan", encoding="utf-8")
        store = _store(engine_paths, artifacts_repository)
        ctx = InternalCommandContext(
            worktree_path=worktree_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_PATH": "PLAN.md"},
            artifact_store=store,
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


def _store(paths: WorkspacePaths, repository: ArtifactsRepository) -> ArtifactStore:
    """Build an ArtifactStore for session wf_abc123 over the engine workspace."""
    return ArtifactStore(Artifacts(paths, repository), "wf_abc123")


def _publish_two_revisions(tmp_path: Path, store: ArtifactStore) -> None:
    """Publish PLAN.md as 'v1' then 'v2' through store."""
    worktree_path = tmp_path / "source"
    worktree_path.mkdir()
    for content in ("v1", "v2"):
        (worktree_path / "PLAN.md").write_text(content, encoding="utf-8")
        result = store.publish(
            worktree_path, name="plan", path_glob="PLAN.md", retention_days=None, provenance=ArtifactProvenance()
        )
        assert result.ok


class HandleArtifactsDownloadTests:
    def test_handle_artifacts_download_artifact_revision_extracts_that_revision(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish_two_revisions(tmp_path, store)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": "1"},
            artifact_store=store,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "completed"
        assert (tmp_path / "out" / "PLAN.md").read_text(encoding="utf-8") == "v1"

    def test_handle_artifacts_download_without_revision_extracts_latest(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish_two_revisions(tmp_path, store)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": ""},
            artifact_store=store,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "completed"
        assert (tmp_path / "out" / "PLAN.md").read_text(encoding="utf-8") == "v2"

    @pytest.mark.parametrize("raw", ["abc", "0", "-2"])
    def test_handle_artifacts_download_invalid_revision_fails_without_download(
        self, raw: str, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish_two_revisions(tmp_path, store)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": raw},
            artifact_store=store,
        )

        outcome = handle_artifacts_download(ctx)

        assert (outcome.status, outcome.exit_code) == ("failed", 1)
        assert outcome.error_message == (
            "Internal command 'artifacts.download' requires ARTIFACT_REVISION to be a positive integer."
        )
        assert not (tmp_path / "out").exists()

    def test_handle_artifacts_download_unknown_revision_fails_with_not_found_text(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish_two_revisions(tmp_path, store)
        ctx = InternalCommandContext(
            worktree_path=tmp_path,
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_DEST": str(tmp_path / "out"), "ARTIFACT_REVISION": "9"},
            artifact_store=store,
        )

        outcome = handle_artifacts_download(ctx)

        assert outcome.status == "failed"
        assert outcome.error_message == "Artifact 'plan' revision 9 not found for session 'wf_abc123'"
