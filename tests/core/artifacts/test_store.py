"""Contract tests for ArtifactStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactDownloadStatus, ArtifactProvenance, ArtifactUploadStatus
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _worktree(tmp_path: Path, content: str) -> Path:
    """Create a worktree whose PLAN.md holds content."""
    worktree_path = tmp_path / f"worktree-{content}"
    worktree_path.mkdir()
    (worktree_path / "PLAN.md").write_text(content, encoding="utf-8")
    return worktree_path


def _publish(store: ArtifactStore, worktree_path: Path) -> None:
    """Publish PLAN.md as 'plan' through store and assert it succeeded."""
    result = store.publish(
        worktree_path, name="plan", path_glob="PLAN.md", retention_days=None, provenance=ArtifactProvenance()
    )
    assert result.ok


class ArtifactStoreTests:
    """Contract tests for ArtifactStore publishing, downloading and reference resolution."""

    def test_publish_records_the_bundle_under_the_store_session(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] ArtifactStore.publish: store bound to 'wf_1' publishing 'plan' -> artifacts_repository.get('wf_1', 'plan') has revision 1 and no row exists for any other session."""
        store = ArtifactStore(Artifacts(artifacts_paths, artifacts_repository), "wf_1")

        _publish(store, _worktree(tmp_path, "plan"))

        record = artifacts_repository.get("wf_1", "plan")
        assert record is not None
        assert record.revision == 1
        assert [stored.session_id for stored in artifacts_repository.list()] == ["wf_1"]

    def test_publish_with_no_matching_files_returns_no_matching_files_status(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] ArtifactStore.publish: a glob matching nothing -> ArtifactUploadStatus.NO_MATCHING_FILES and no row for 'wf_1'."""
        store = ArtifactStore(Artifacts(artifacts_paths, artifacts_repository), "wf_1")

        result = store.publish(
            _worktree(tmp_path, "plan"),
            name="plan",
            path_glob="MISSING.md",
            retention_days=None,
            provenance=ArtifactProvenance(),
        )

        assert result.status == ArtifactUploadStatus.NO_MATCHING_FILES
        assert artifacts_repository.get("wf_1", "plan") is None

    @pytest.mark.parametrize(
        ("override", "expected"),
        [pytest.param(None, "mine", id="default-session"), pytest.param("wf_other", "other", id="override-session")],
    )
    def test_download_reads_from_default_or_override_session(
        self,
        override: str | None,
        expected: str,
        tmp_path: Path,
        artifacts_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        """[tier-1/integration] ArtifactStore.download: store bound to 'wf_1'; session_id=None extracts the 'wf_1' bundle, session_id='wf_other' extracts the 'wf_other' bundle into dest."""
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        _publish(ArtifactStore(artifacts, "wf_1"), _worktree(tmp_path, "mine"))
        _publish(ArtifactStore(artifacts, "wf_other"), _worktree(tmp_path, "other"))
        dest = tmp_path / "out"

        result = ArtifactStore(artifacts, "wf_1").download(name="plan", dest=dest, session_id=override)

        assert result.status == ArtifactDownloadStatus.OK
        assert (dest / "PLAN.md").read_text(encoding="utf-8") == expected

    def test_download_from_session_without_artifact_returns_not_found_for_that_session(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] ArtifactStore.download: session_id='wf_other' with nothing published -> ArtifactDownloadResult.status == NOT_FOUND and session_id == 'wf_other'."""
        store = ArtifactStore(Artifacts(artifacts_paths, artifacts_repository), "wf_1")

        result = store.download(name="plan", dest=tmp_path / "out", session_id="wf_other")

        assert result.status == ArtifactDownloadStatus.NOT_FOUND
        assert result.session_id == "wf_other"

    def test_resolve_references_sees_only_the_store_session(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] ArtifactStore.resolve_references: 'plan' published in 'wf_1' (rev 2) and 'wf_other' (rev 5); store 'wf_1' resolving {'plan': {'revision'}} -> references == {'plan': {'revision': '2'}}; {'ghost': {'revision'}} -> references == {}."""
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        store = ArtifactStore(artifacts, "wf_1")
        other = ArtifactStore(artifacts, "wf_other")
        worktree_path = _worktree(tmp_path, "plan")
        for _ in range(2):
            _publish(store, worktree_path)
        for _ in range(5):
            _publish(other, worktree_path)

        assert store.resolve_references({"plan": {"revision"}}).references == {"plan": {"revision": "2"}}
        assert store.resolve_references({"ghost": {"revision"}}).references == {}
