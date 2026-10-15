"""Contract tests for ArtifactNamespaceProvider."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.inputs.exceptions import NamespaceResolutionError
from dovo.engine.executors.artifact_namespace import ArtifactNamespaceProvider


def _publish(
    store: ArtifactStore, tmp_path: Path, files: dict[str, str], *, name: str = "plan", times: int = 1
) -> None:
    """Publish files as artifact `name` `times` times through store."""
    source = tmp_path / f"source-{name}"
    source.mkdir(exist_ok=True)
    for relative_path, content in files.items():
        (source / relative_path).write_text(content, encoding="utf-8")
    for _ in range(times):
        result = store.publish(source, name=name, path_glob="*", retention_days=None, provenance=ArtifactProvenance())
        assert result.ok


def _store(paths: WorkspacePaths, repository: ArtifactsRepository) -> ArtifactStore:
    """Build an ArtifactStore for session wf_1 over the engine workspace."""
    return ArtifactStore(Artifacts(paths, repository), "wf_1")


class ArtifactNamespaceProviderTests:
    @pytest.mark.parametrize("field_name", ["revision", "dir"])
    def test_known_field_resolves_from_latest_revision(
        self, field_name: str, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish(store, tmp_path, {"PLAN.md": "plan"}, times=2)
        record = artifacts_repository.get("wf_1", "plan")
        assert record is not None
        expected = {"revision": "2", "dir": str(record.path)}[field_name]

        assert ArtifactNamespaceProvider(store).resolve(f"artifacts.plan.{field_name}") == expected

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("artifacts.ghost.digest", id="unknown-artifact"),
            pytest.param("artifacts.plan.bogus", id="unknown-field"),
        ],
    )
    def test_unknown_artifact_or_field_resolves_to_empty_string(
        self, path: str, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish(store, tmp_path, {"PLAN.md": "plan"})

        assert ArtifactNamespaceProvider(store).resolve(path) == ""

    def test_dotted_artifact_name_uses_last_segment_as_field(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish(store, tmp_path, {"PLAN.md": "plan"}, name="team.plan")
        record = artifacts_repository.get("wf_1", "team.plan")
        assert record is not None
        assert record.digest

        assert ArtifactNamespaceProvider(store).resolve("artifacts.team.plan.digest") == record.digest

    @pytest.mark.parametrize("path", ["inputs.x", "artifacts.plan"])
    def test_non_artifact_path_returns_none(self, path: str) -> None:
        assert ArtifactNamespaceProvider(None).resolve(path) is None

    def test_missing_store_resolves_well_formed_key_to_empty_string(self) -> None:
        assert ArtifactNamespaceProvider(None).resolve("artifacts.plan.revision") == ""

    def test_content_of_multi_file_artifact_raises_with_classified_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish(store, tmp_path, {"a.md": "a", "b.md": "b"})

        with pytest.raises(NamespaceResolutionError) as raised:
            ArtifactNamespaceProvider(store).resolve("artifacts.plan.content")

        assert str(raised.value) == (
            "Artifact 'plan' cannot be inlined (ARTIFACT_CONTENT_UNAVAILABLE): "
            "artifact has 2 files, content needs exactly one"
        )

    def test_revision_of_multi_file_artifact_never_reads_content(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        store = _store(engine_paths, artifacts_repository)
        _publish(store, tmp_path, {"a.md": "a", "b.md": "b"})

        assert ArtifactNamespaceProvider(store).resolve("artifacts.plan.revision") == "1"
