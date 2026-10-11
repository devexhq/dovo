"""Contract tests for ArtifactStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.artifact_store import ArtifactStore


class ArtifactStoreTests:
    """Contract tests for ArtifactStore construction, publishing and reference resolution."""

    def test_from_context_returns_store_when_dir_and_db_present(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] ArtifactStore.from_context: artifacts_dir and db given -> store with the same artifacts_dir, db and session_id."""
        store = ArtifactStore.from_context(tmp_path, artifacts_repository, "wf_1")

        assert store is not None
        assert (store.artifacts_dir, store.db, store.session_id) == (tmp_path, artifacts_repository, "wf_1")

    @pytest.mark.parametrize("missing", ["dir", "db", "both"])
    def test_from_context_returns_none_when_either_is_missing(
        self, missing: str, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] ArtifactStore.from_context: artifacts_dir=None and/or db=None -> None."""
        artifacts_dir = None if missing in ("dir", "both") else tmp_path
        db = None if missing in ("db", "both") else artifacts_repository

        assert ArtifactStore.from_context(artifacts_dir, db, "wf_1") is None

    def test_publish_and_resolve_references_round_trip_latest_revision(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] ArtifactStore.publish/resolve_references: two publishes of 'plan' then resolving {'plan': {'revision'}} -> references == {'plan': {'revision': '2'}}."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        (worktree_path / "PLAN.md").write_text("plan", encoding="utf-8")
        store = ArtifactStore(tmp_path / "artifacts", artifacts_repository, "wf_1")
        for _ in range(2):
            published = store.publish(
                worktree_path,
                name="plan",
                path_glob="PLAN.md",
                retention_days=None,
                provenance=ArtifactProvenance(),
            )
            assert published.ok

        result = store.resolve_references({"plan": {"revision"}})

        assert result.references == {"plan": {"revision": "2"}}
