"""Contract tests for the Artifacts entrypoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import (
    ArtifactProvenance,
    ArtifactReferencesStatus,
    ArtifactsPruneStatus,
    ArtifactUploadStatus,
)
from dovo.core.config.models import DovoConfig, ProjectConfig, PruneConfig
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _worktree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a worktree holding the given files."""
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir(exist_ok=True)
    for relative_path, content in files.items():
        (worktree_path / relative_path).write_text(content, encoding="utf-8")
    return worktree_path


def _publish_plan(artifacts: Artifacts, worktree_path: Path, glob: str = "PLAN.md") -> None:
    """Publish 'plan' for session wf_1 and assert it succeeded."""
    result = artifacts.publish("wf_1", "plan", glob, worktree_path=worktree_path, provenance=ArtifactProvenance())
    assert result.ok


class ArtifactsTests:
    """Contract tests for Artifacts publishing, reference resolution and pruning."""

    def test_publish_stores_bundle_under_given_session(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)

        _publish_plan(artifacts, _worktree(tmp_path, {"PLAN.md": "plan"}))

        assert [record.name for record in artifacts.list(session_id="wf_1").artifacts] == ["plan"]

    def test_publish_with_no_matching_files_returns_no_matching_files_status(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        result = Artifacts(artifacts_paths, artifacts_repository).publish(
            "wf_1", "plan", "PLAN.md", worktree_path=_worktree(tmp_path, {}), provenance=ArtifactProvenance()
        )

        assert result.status == ArtifactUploadStatus.NO_MATCHING_FILES

    def test_resolve_references_returns_latest_revision_fields(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        worktree_path = _worktree(tmp_path, {"PLAN.md": "plan"})
        _publish_plan(artifacts, worktree_path)
        _publish_plan(artifacts, worktree_path)

        result = artifacts.resolve_references("wf_1", {"plan": {"revision"}})

        assert result.references == {"plan": {"revision": "2"}}

    def test_resolve_references_content_of_multi_file_artifact_is_unavailable(
        self, tmp_path: Path, artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        artifacts = Artifacts(artifacts_paths, artifacts_repository)
        _publish_plan(artifacts, _worktree(tmp_path, {"a.md": "a", "b.md": "b"}), glob="*.md")

        result = artifacts.resolve_references("wf_1", {"plan": {"content"}})

        assert result.status == ArtifactReferencesStatus.CONTENT_UNAVAILABLE

    @pytest.mark.parametrize(
        ("remove_expired", "expected"),
        [
            pytest.param(None, ArtifactsPruneStatus.DISABLED, id="no-config"),
            pytest.param(True, ArtifactsPruneStatus.OK, id="config-enabled"),
        ],
    )
    def test_prune_resolves_remove_expired_from_config(
        self,
        remove_expired: bool | None,
        expected: ArtifactsPruneStatus,
        artifacts_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        config = (
            None
            if remove_expired is None
            else DovoConfig(
                version=1,
                project=ProjectConfig(name="test-project"),
                prune=PruneConfig(remove_expired_artifacts=remove_expired),
            )
        )

        result = Artifacts(artifacts_paths, artifacts_repository).prune(config=config)

        assert result.status == expected
