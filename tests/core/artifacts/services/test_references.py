"""Contract tests for the resolve_artifact_references service."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactProvenance, ArtifactReferencesStatus
from dovo.core.artifacts.services.references import resolve_artifact_references
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.db.repositories.artifacts import ArtifactsRepository

_SESSION_ID = "wf_abc123"


def _publish_plan(tmp_path: Path, repository: ArtifactsRepository, files: dict[str, bytes]) -> tuple[Path, str | None]:
    """Publish the given files as the next revision of 'plan' and return (revision directory, digest)."""
    worktree_path = tmp_path / f"worktree-{len(list((tmp_path).glob('worktree-*')))}"
    for relative_path, content in files.items():
        target = worktree_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    result = publish_artifact(
        worktree_path,
        tmp_path / "artifacts",
        repository,
        session_id=_SESSION_ID,
        name="plan",
        path_glob="*",
        retention_days=None,
        provenance=ArtifactProvenance(),
    )
    assert result.ok
    return tmp_path / "artifacts" / _SESSION_ID / "plan" / str(result.revision), result.digest


class ResolveArtifactReferencesTests:
    """Contract tests for resolve_artifact_references."""

    def test_latest_revision_values_for_requested_fields(
        self, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        _publish_plan(tmp_path, artifacts_repository, {"PLAN.md": b"first"})
        revision_dir, digest = _publish_plan(tmp_path, artifacts_repository, {"PLAN.md": b"second"})

        result = resolve_artifact_references(
            artifacts_repository,
            session_id=_SESSION_ID,
            requested={"plan": {"revision", "digest", "dir", "content"}},
        )

        assert result.status == ArtifactReferencesStatus.OK
        assert result.references == {
            "plan": {"revision": "2", "digest": digest, "dir": str(revision_dir), "content": "second"}
        }

    def test_unknown_artifact_is_absent_from_references(self, artifacts_repository: ArtifactsRepository) -> None:
        result = resolve_artifact_references(
            artifacts_repository, session_id=_SESSION_ID, requested={"ghost": {"digest"}}
        )

        assert result.status == ArtifactReferencesStatus.OK
        assert result.references == {}

    @pytest.mark.parametrize(
        ("files", "reason"),
        [
            pytest.param(
                {"a.md": b"a", "b.md": b"b"}, "artifact has 2 files, content needs exactly one", id="two_files"
            ),
            pytest.param({"bin.dat": b"\xff\xfe\x00"}, "file is not valid UTF-8", id="binary"),
            pytest.param({"big.txt": b"x" * 65537}, "file is 65537 bytes, limit is 65536", id="oversized"),
        ],
    )
    def test_content_unavailable_returns_classified_error(
        self, files: dict[str, bytes], reason: str, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        _publish_plan(tmp_path, artifacts_repository, files)

        result = resolve_artifact_references(
            artifacts_repository, session_id=_SESSION_ID, requested={"plan": {"content"}}
        )

        assert result.status == ArtifactReferencesStatus.CONTENT_UNAVAILABLE
        assert result.errors == [f"Artifact 'plan' cannot be inlined (ARTIFACT_CONTENT_UNAVAILABLE): {reason}"]

    def test_file_exactly_65536_bytes_is_inlined(
        self, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        _publish_plan(tmp_path, artifacts_repository, {"big.txt": b"x" * 65536})

        result = resolve_artifact_references(
            artifacts_repository, session_id=_SESSION_ID, requested={"plan": {"content"}}
        )

        assert result.status == ArtifactReferencesStatus.OK
        assert result.references == {"plan": {"content": "x" * 65536}}

    def test_unreadable_manifest_returns_classified_error(
        self, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        revision_dir = _publish_plan(tmp_path, artifacts_repository, {"PLAN.md": b"text"})[0]
        (revision_dir / "manifest.json").unlink()

        result = resolve_artifact_references(
            artifacts_repository, session_id=_SESSION_ID, requested={"plan": {"content"}}
        )

        assert result.status == ArtifactReferencesStatus.CONTENT_UNAVAILABLE
        assert result.errors[0].startswith(
            "Artifact 'plan' cannot be inlined (ARTIFACT_CONTENT_UNAVAILABLE): cannot read file:"
        )

    def test_legacy_row_without_digest_renders_empty_digest(self, artifacts_repository: ArtifactsRepository) -> None:
        artifacts_repository.create(
            _SESSION_ID,
            "plan",
            "/tmp/legacy/plan",
            revision=1,
            size_bytes=1,
            file_count=1,
            expires_at=None,
            digest=None,
            step_id=None,
            attempt=None,
            loop_iteration=None,
        )

        result = resolve_artifact_references(
            artifacts_repository, session_id=_SESSION_ID, requested={"plan": {"digest"}}
        )

        assert result.references == {"plan": {"digest": ""}}

    def test_dir_and_revision_resolve_even_when_content_would_fail(
        self, artifacts_repository: ArtifactsRepository, tmp_path: Path
    ) -> None:
        """Resolve only the requested 'dir' and 'revision' fields; content is not evaluated unless requested."""
        revision_dir = _publish_plan(tmp_path, artifacts_repository, {"a.md": b"a", "b.md": b"b"})[0]

        result = resolve_artifact_references(
            artifacts_repository, session_id=_SESSION_ID, requested={"plan": {"dir", "revision"}}
        )

        assert result.status == ArtifactReferencesStatus.OK
        assert result.references == {"plan": {"dir": str(revision_dir), "revision": "1"}}
