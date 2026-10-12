"""Contract tests for step postconditions."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.models import StepDispatchOutcome
from dovo.engine.executors.postconditions import (
    ArtifactPublishPostcondition,
    AssertionPostcondition,
    build_postconditions,
)

_COMPLETED = StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")


def _step(**extra: object) -> StepDefinition:
    """Build a command step named 'check' with the given extra fields."""
    return StepDefinition.model_validate(
        {"id": "check", "name": "check", "type": "command", "command": "true", **extra}
    )


def _store(paths: WorkspacePaths, repository: ArtifactsRepository) -> ArtifactStore:
    """Build an ArtifactStore for session wf_1 over the engine workspace."""
    return ArtifactStore(Artifacts(paths, repository), "wf_1")


class AssertionPostconditionTests:
    """[tier-1/unit] AssertionPostcondition.apply: assert: checks downgrade a completed outcome."""

    def test_passing_assertions_return_outcome_unchanged(self, tmp_path: Path) -> None:
        """[tier-1/unit] AssertionPostcondition.apply: completed outcome and a satisfied assert_ -> the same StepDispatchOutcome."""
        (tmp_path / "present.bin").write_text("x", encoding="utf-8")
        step = _step(assert_={"file_exists": "present.bin"})

        assert AssertionPostcondition(step, tmp_path).apply(_COMPLETED, 1) == _COMPLETED

    def test_failing_assertion_returns_failed_copy_with_diagnostic(self, tmp_path: Path) -> None:
        """[tier-1/unit] AssertionPostcondition.apply: file_exists on 'missing.bin' -> status 'failed' and error_message starts with "Step 'check' failed assertion checks:"."""
        step = _step(assert_={"file_exists": "missing.bin"})

        outcome = AssertionPostcondition(step, tmp_path).apply(_COMPLETED, 1)

        assert outcome.status == "failed"
        assert outcome.error_message is not None
        assert outcome.error_message.startswith("Step 'check' failed assertion checks:")


class ArtifactPublishPostconditionTests:
    """[tier-1/integration] ArtifactPublishPostcondition.apply: declared artifacts publish after success."""

    def test_declared_artifact_is_published_and_outcome_unchanged(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] ArtifactPublishPostcondition.apply: PLAN.md present, required spec 'plan' -> outcome unchanged and one row revision 1 for session wf_1."""
        (tmp_path / "PLAN.md").write_text("plan", encoding="utf-8")
        postcondition = ArtifactPublishPostcondition(
            _step(artifacts=[{"name": "plan", "path": "PLAN.md"}]),
            worktree_path=tmp_path,
            store=_store(engine_paths, artifacts_repository),
            loop_iteration=None,
            warnings=[],
        )

        assert postcondition.apply(_COMPLETED, 1) == _COMPLETED
        assert [(record.session_id, record.revision) for record in artifacts_repository.list()] == [("wf_1", 1)]

    def test_missing_required_artifact_returns_failed_copy(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] ArtifactPublishPostcondition.apply: required 'PLAN.md' absent -> status 'failed' and the ARTIFACT_NOT_PRODUCED error_message."""
        postcondition = ArtifactPublishPostcondition(
            _step(artifacts=[{"name": "plan", "path": "PLAN.md"}]),
            worktree_path=tmp_path,
            store=_store(engine_paths, artifacts_repository),
            loop_iteration=None,
            warnings=[],
        )

        outcome = postcondition.apply(_COMPLETED, 1)

        assert outcome.status == "failed"
        assert outcome.error_message == "Artifact 'plan' matched no files at 'PLAN.md' (ARTIFACT_NOT_PRODUCED)."

    def test_optional_artifact_failure_appends_warning_and_keeps_outcome(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] ArtifactPublishPostcondition.apply: required=False spec with no match -> outcome unchanged and warnings == ["Failed to publish artifact 'plan': ..."]."""
        warnings: list[str] = []
        postcondition = ArtifactPublishPostcondition(
            _step(artifacts=[{"name": "plan", "path": "PLAN.md", "required": False}]),
            worktree_path=tmp_path,
            store=_store(engine_paths, artifacts_repository),
            loop_iteration=None,
            warnings=warnings,
        )

        assert postcondition.apply(_COMPLETED, 1) == _COMPLETED
        assert warnings == [
            "Failed to publish artifact 'plan': Artifact 'plan' matched no files at 'PLAN.md' (ARTIFACT_NOT_PRODUCED)."
        ]


class BuildPostconditionsTests:
    """[tier-1/unit] build_postconditions: evaluation order."""

    def test_returns_assertions_then_artifact_publishing(self, tmp_path: Path) -> None:
        """[tier-1/unit] build_postconditions: returns [AssertionPostcondition, ArtifactPublishPostcondition] in that order."""
        postconditions = build_postconditions(
            _step(), worktree_path=tmp_path, store=None, loop_iteration=None, warnings=[]
        )

        assert [type(postcondition) for postcondition in postconditions] == [
            AssertionPostcondition,
            ArtifactPublishPostcondition,
        ]
