"""Contract tests for ArtifactNamespaceProvider."""

from __future__ import annotations

import pytest

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.inputs.exceptions import NamespaceResolutionError
from dovo.engine.executors.artifact_namespace import ArtifactNamespaceProvider
from tests.conftest import PublishArtifact


class ArtifactNamespaceProviderTests:
    @pytest.mark.parametrize("field_name", ["revision", "dir"])
    def test_known_field_resolves_from_latest_revision(
        self,
        field_name: str,
        artifacts_repository: ArtifactsRepository,
        artifact_store: ArtifactStore,
        publish_artifact: PublishArtifact,
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "plan"})
        publish_artifact("plan", {"PLAN.md": "plan"})
        record = artifacts_repository.get("wf_1", "plan")
        assert record is not None
        expected = {"revision": "2", "dir": str(record.path)}[field_name]

        assert ArtifactNamespaceProvider(artifact_store).resolve(f"artifacts.plan.{field_name}") == expected

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("artifacts.ghost.digest", id="unknown-artifact"),
            pytest.param("artifacts.plan.bogus", id="unknown-field"),
        ],
    )
    def test_unknown_artifact_or_field_resolves_to_empty_string(
        self, path: str, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "plan"})

        assert ArtifactNamespaceProvider(artifact_store).resolve(path) == ""

    def test_dotted_artifact_name_uses_last_segment_as_field(
        self,
        artifacts_repository: ArtifactsRepository,
        artifact_store: ArtifactStore,
        publish_artifact: PublishArtifact,
    ) -> None:
        publish_artifact("team.plan", {"PLAN.md": "plan"})
        record = artifacts_repository.get("wf_1", "team.plan")
        assert record is not None
        assert record.digest

        assert ArtifactNamespaceProvider(artifact_store).resolve("artifacts.team.plan.digest") == record.digest

    @pytest.mark.parametrize("path", ["inputs.x", "artifacts.plan"])
    def test_non_artifact_path_returns_none(self, path: str) -> None:
        assert ArtifactNamespaceProvider(None).resolve(path) is None

    def test_missing_store_resolves_well_formed_key_to_empty_string(self) -> None:
        assert ArtifactNamespaceProvider(None).resolve("artifacts.plan.revision") == ""

    def test_content_of_multi_file_artifact_raises_with_classified_message(
        self, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("plan", {"a.md": "a", "b.md": "b"})

        with pytest.raises(NamespaceResolutionError) as raised:
            ArtifactNamespaceProvider(artifact_store).resolve("artifacts.plan.content")

        assert str(raised.value) == (
            "Artifact 'plan' cannot be inlined (ARTIFACT_CONTENT_UNAVAILABLE): "
            "artifact has 2 files, content needs exactly one"
        )
