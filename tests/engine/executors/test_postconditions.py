"""Contract tests for step postconditions."""

from __future__ import annotations

from pathlib import Path

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


class AssertionPostconditionTests:
    def test_passing_assertions_return_outcome_unchanged(self, tmp_path: Path) -> None:
        (tmp_path / "present.bin").write_text("x", encoding="utf-8")
        step = _step(assert_={"file_exists": "present.bin"})

        assert AssertionPostcondition(step, tmp_path).apply(_COMPLETED, 1) == _COMPLETED

    def test_failing_assertion_returns_failed_copy_with_diagnostic(self, tmp_path: Path) -> None:
        step = _step(assert_={"file_exists": "missing.bin"})

        outcome = AssertionPostcondition(step, tmp_path).apply(_COMPLETED, 1)

        assert outcome.status == "failed"
        assert outcome.error_message is not None
        assert outcome.error_message.startswith("Step 'check' failed assertion checks:")


class ArtifactPublishPostconditionTests:
    def test_declared_artifact_is_published_and_outcome_unchanged(
        self, tmp_path: Path, artifacts_repository: ArtifactsRepository, artifact_store: ArtifactStore
    ) -> None:
        (tmp_path / "PLAN.md").write_text("plan", encoding="utf-8")
        postcondition = ArtifactPublishPostcondition(
            _step(artifacts=[{"name": "plan", "path": "PLAN.md"}]),
            worktree_path=tmp_path,
            store=artifact_store,
            loop_iteration=None,
            warnings=[],
        )

        assert postcondition.apply(_COMPLETED, 1) == _COMPLETED
        assert [(record.session_id, record.revision) for record in artifacts_repository.list()] == [("wf_1", 1)]

    def test_missing_required_artifact_returns_failed_copy(self, tmp_path: Path, artifact_store: ArtifactStore) -> None:
        postcondition = ArtifactPublishPostcondition(
            _step(artifacts=[{"name": "plan", "path": "PLAN.md"}]),
            worktree_path=tmp_path,
            store=artifact_store,
            loop_iteration=None,
            warnings=[],
        )

        outcome = postcondition.apply(_COMPLETED, 1)

        assert outcome.status == "failed"
        assert outcome.error_message == "Artifact 'plan' matched no files at 'PLAN.md' (ARTIFACT_NOT_PRODUCED)."

    def test_optional_artifact_failure_appends_warning_and_keeps_outcome(
        self, tmp_path: Path, artifact_store: ArtifactStore
    ) -> None:
        warnings: list[str] = []
        postcondition = ArtifactPublishPostcondition(
            _step(artifacts=[{"name": "plan", "path": "PLAN.md", "required": False}]),
            worktree_path=tmp_path,
            store=artifact_store,
            loop_iteration=None,
            warnings=warnings,
        )

        assert postcondition.apply(_COMPLETED, 1) == _COMPLETED
        assert warnings == [
            "Failed to publish artifact 'plan': Artifact 'plan' matched no files at 'PLAN.md' (ARTIFACT_NOT_PRODUCED)."
        ]


class BuildPostconditionsTests:
    def test_returns_assertions_then_artifact_publishing(self, tmp_path: Path) -> None:
        postconditions = build_postconditions(
            _step(), worktree_path=tmp_path, store=None, loop_iteration=None, warnings=[]
        )

        assert [type(postcondition) for postcondition in postconditions] == [
            AssertionPostcondition,
            ArtifactPublishPostcondition,
        ]
