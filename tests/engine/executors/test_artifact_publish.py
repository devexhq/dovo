"""Contract tests for publish_declared_artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.artifacts.models import ArtifactUploadResult, ArtifactUploadStatus
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition, StepType
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.artifact_publish import ArtifactPublishOutcome, publish_declared_artifacts
from tests.harness.diagnostic_log import diagnostic_record, read_single_event


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


class PublishDeclaredArtifactsTests:
    """Contract tests for publish_declared_artifacts."""

    @pytest.mark.parametrize(
        ("required", "error_message", "warnings"),
        [
            pytest.param(
                True,
                "Failed to publish artifact 'plan': boom",
                [],
                id="required-fails-step",
            ),
            pytest.param(False, None, ["Failed to publish artifact 'plan': boom"], id="optional-warns"),
        ],
    )
    def test_publish_exception_maps_by_required_flag(
        self,
        required: bool,
        error_message: str | None,
        warnings: list[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        artifact_store: ArtifactStore,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """Map a raising publish by the required flag, carry its engine.step record in diagnostics and write one event."""

        def _raising_publish(*_args: object, **_kwargs: object) -> ArtifactUploadResult:
            raise ValueError("boom")

        monkeypatch.setattr("dovo.core.artifacts.artifacts.publish_artifact", _raising_publish)
        worktree_path = _worktree(tmp_path, "PLAN.md")

        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md", required=required)),
            worktree_path=worktree_path,
            store=artifact_store,
            attempt=1,
            loop_iteration=None,
        )

        assert outcome == ArtifactPublishOutcome(
            error_message=error_message, warnings=warnings, diagnostics=[diagnostic_record(frozen_diagnostic_clock)]
        )
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"], event["context"]) == (
            "error",
            "engine.step",
            {"step_id": "plan-step", "path": str(worktree_path)},
        )

    def test_error_result_without_detail_uses_generic_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_store: ArtifactStore
    ) -> None:
        monkeypatch.setattr(
            "dovo.core.artifacts.artifacts.publish_artifact",
            lambda *_args, **_kwargs: ArtifactUploadResult(status=ArtifactUploadStatus.ERROR),
        )

        outcome = publish_declared_artifacts(
            _step(ArtifactPublishSpec(name="plan", path="PLAN.md")),
            worktree_path=_worktree(tmp_path, "PLAN.md"),
            store=artifact_store,
            attempt=1,
            loop_iteration=None,
        )

        assert outcome.error_message == "Failed to publish artifact 'plan': publish failed"

    def test_no_session_returns_empty_outcome(self, tmp_path: Path) -> None:
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
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, artifact_store: ArtifactStore
    ) -> None:
        step = _step(
            ArtifactPublishSpec(name="first", path="a.txt"),
            ArtifactPublishSpec(name="second", path="missing.txt"),
            ArtifactPublishSpec(name="third", path="c.txt"),
        )

        outcome = publish_declared_artifacts(
            step,
            worktree_path=_worktree(tmp_path, "a.txt", "c.txt"),
            store=artifact_store,
            attempt=1,
            loop_iteration=None,
        )

        assert outcome.error_message == ("Artifact 'second' matched no files at 'missing.txt'.")
        stored = {record.name: record.revision for record in artifacts_repository.list(session_id="wf_1")}
        assert stored == {"first": 1}
