"""Contract tests for publish_declared_artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactUploadResult, ArtifactUploadStatus
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition, StepType
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.artifact_publish import ArtifactPublishOutcome, publish_declared_artifacts

_MISSING_PLAN = "Artifact 'plan' matched no files at 'PLAN.md' (ARTIFACT_NOT_PRODUCED)."


def _step(*specs: ArtifactPublishSpec) -> StepDefinition:
    """Build a command step declaring the given artifact specs."""
    return StepDefinition(id="plan-step", type=StepType.COMMAND, command="true", artifacts=list(specs))


def _worktree(tmp_path: Path, *files: str) -> Path:
    """Create a worktree directory holding the named files."""
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    for name in files:
        (worktree_path / name).write_text(name, encoding="utf-8")
    return worktree_path


def _store(paths: WorkspacePaths, repository: ArtifactsRepository) -> ArtifactStore:
    """Build an ArtifactStore for session wf_1 over the engine workspace."""
    return ArtifactStore(Artifacts(paths, repository), "wf_1")


class PublishDeclaredArtifactsTests:
    """Contract tests for publish_declared_artifacts."""

    def test_required_spec_matching_nothing_returns_not_produced_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] publish_declared_artifacts: required spec path 'PLAN.md' with an empty worktree -> error_message is the ARTIFACT_NOT_PRODUCED text and warnings == []."""
        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md")),
            worktree_path=_worktree(tmp_path),
            store=_store(engine_paths, artifacts_repository),
            attempt=1,
            loop_iteration=None,
        )

        assert outcome == ArtifactPublishOutcome(error_message=_MISSING_PLAN, warnings=[])

    def test_optional_spec_matching_nothing_returns_warning_and_no_error(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] publish_declared_artifacts: required=False spec with no match -> error_message is None and a 'Failed to publish artifact' warning carrying the not-produced text."""
        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md", required=False)),
            worktree_path=_worktree(tmp_path),
            store=_store(engine_paths, artifacts_repository),
            attempt=1,
            loop_iteration=None,
        )

        assert outcome == ArtifactPublishOutcome(
            error_message=None, warnings=[f"Failed to publish artifact 'plan': {_MISSING_PLAN}"]
        )

    @pytest.mark.parametrize(
        ("required", "expected"),
        [
            pytest.param(
                True,
                ArtifactPublishOutcome(
                    error_message="Failed to publish artifact 'plan': boom (ARTIFACT_PUBLISH_FAILED)"
                ),
                id="required-fails-step",
            ),
            pytest.param(
                False,
                ArtifactPublishOutcome(warnings=["Failed to publish artifact 'plan': boom"]),
                id="optional-warns",
            ),
        ],
    )
    def test_publish_exception_maps_by_required_flag(
        self,
        required: bool,
        expected: ArtifactPublishOutcome,
        tmp_path: Path,
        engine_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/unit] publish_declared_artifacts: publish raising ValueError('boom') -> required gives the ARTIFACT_PUBLISH_FAILED error; optional gives that text without the code as a warning."""

        def _raising_publish(*_args: object, **_kwargs: object) -> ArtifactUploadResult:
            raise ValueError("boom")

        monkeypatch.setattr("dovo.core.artifacts.artifacts.publish_artifact", _raising_publish)

        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md", required=required)),
            worktree_path=_worktree(tmp_path, "PLAN.md"),
            store=_store(engine_paths, artifacts_repository),
            attempt=1,
            loop_iteration=None,
        )

        assert outcome == expected

    def test_error_result_without_detail_uses_generic_text(
        self,
        tmp_path: Path,
        engine_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[tier-1/unit] publish_declared_artifacts: a failed publish result carrying no errors -> detail 'publish failed'."""
        monkeypatch.setattr(
            "dovo.core.artifacts.artifacts.publish_artifact",
            lambda *_args, **_kwargs: ArtifactUploadResult(status=ArtifactUploadStatus.ERROR),
        )

        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md")),
            worktree_path=_worktree(tmp_path, "PLAN.md"),
            store=_store(engine_paths, artifacts_repository),
            attempt=1,
            loop_iteration=None,
        )

        assert outcome.error_message == "Failed to publish artifact 'plan': publish failed (ARTIFACT_PUBLISH_FAILED)"

    def test_no_session_returns_empty_outcome(self, tmp_path: Path) -> None:
        """[tier-1/unit] publish_declared_artifacts: store=None -> empty ArtifactPublishOutcome and nothing written."""
        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md")),
            worktree_path=_worktree(tmp_path, "PLAN.md"),
            store=None,
            attempt=1,
            loop_iteration=None,
        )

        assert outcome == ArtifactPublishOutcome(error_message=None, warnings=[])
        assert not (tmp_path / "artifacts").exists()

    def test_stops_at_first_required_failure_keeping_earlier_revision(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] publish_declared_artifacts: specs [ok, missing-required, ok] -> error_message names the second spec, the first has revision 1 in the DB, the third has no row."""
        step = _step(
            ArtifactPublishSpec(name="first", path="a.txt"),
            ArtifactPublishSpec(name="second", path="missing.txt"),
            ArtifactPublishSpec(name="third", path="c.txt"),
        )

        outcome = publish_declared_artifacts(
            step,
            worktree_path=_worktree(tmp_path, "a.txt", "c.txt"),
            store=_store(engine_paths, artifacts_repository),
            attempt=1,
            loop_iteration=None,
        )

        assert outcome.error_message == ("Artifact 'second' matched no files at 'missing.txt' (ARTIFACT_NOT_PRODUCED).")
        stored = {record.name: record.revision for record in artifacts_repository.list(session_id="wf_1")}
        assert stored == {"first": 1}
