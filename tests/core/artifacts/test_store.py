"""Contract tests for ArtifactStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactDownloadStatus, ArtifactProvenance
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
        store = ArtifactStore(Artifacts(artifacts_paths, artifacts_repository), "wf_1")

        _publish(store, _worktree(tmp_path, "plan"))

        record = artifacts_repository.get("wf_1", "plan")
        assert record is not None
        assert record.revision == 1
        assert [stored.session_id for stored in artifacts_repository.list()] == ["wf_1"]

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
        store = ArtifactStore(Artifacts(artifacts_paths, artifacts_repository), "wf_1")

        result = store.download(name="plan", dest=tmp_path / "out", session_id="wf_other")

        assert result.status == ArtifactDownloadStatus.NOT_FOUND
        assert result.session_id == "wf_other"

    def test_resolve_references_sees_only_the_store_session(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        store = ArtifactStore(artifacts, "wf_1")
        other = ArtifactStore(artifacts, "wf_other")
        worktree_path = _worktree(tmp_path, "plan")
        for round_number in range(5):
            (worktree_path / "PLAN.md").write_text(f"plan {round_number}", encoding="utf-8")
            if round_number < 2:
                _publish(store, worktree_path)
            _publish(other, worktree_path)

        assert store.resolve_references({"plan": {"revision"}}).references == {"plan": {"revision": "2"}}
        assert store.resolve_references({"ghost": {"revision"}}).references == {}
