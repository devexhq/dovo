"""Contract tests for auto_publish_step_artifacts."""

from __future__ import annotations

from pathlib import Path

from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.runtime.artifact_publish import auto_publish_step_artifacts
from worktree.core.step.models import ArtifactPublishSpec, StepDefinition, StepType


class AutoPublishStepArtifactsTests:
    """[tier-1/unit] auto_publish_step_artifacts: no-session short-circuit and non-fatal publish failures."""

    def test_auto_publish_step_artifacts_no_session_returns_no_warnings(self, tmp_path: Path) -> None:
        """[tier-1/unit] auto_publish_step_artifacts: artifacts_dir=None and artifacts_db=None -> returns [] without invoking publish_artifact."""
        step = StepDefinition(
            id="s1",
            type=StepType.COMMAND,
            command="echo hi",
            artifacts=[ArtifactPublishSpec(name="coverage", path="htmlcov/**")],
        )

        warnings = auto_publish_step_artifacts(
            step,
            sandbox_path=tmp_path,
            session_id="wf_abc123",
            artifacts_dir=None,
            artifacts_db=None,
        )

        assert warnings == []

    def test_auto_publish_step_artifacts_publish_failure_returns_warning_not_raise(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/unit] auto_publish_step_artifacts: a step declaring artifacts: [{name, path}] whose glob matches nothing returns one warning string naming the artifact, and raises nothing."""
        step = StepDefinition(
            id="s1",
            type=StepType.COMMAND,
            command="echo hi",
            artifacts=[ArtifactPublishSpec(name="coverage", path="htmlcov/**")],
        )

        warnings = auto_publish_step_artifacts(
            step,
            sandbox_path=tmp_path,
            session_id="wf_abc123",
            artifacts_dir=tmp_path / "artifacts",
            artifacts_db=artifacts_repository,
        )

        assert len(warnings) == 1
        assert "coverage" in warnings[0]
