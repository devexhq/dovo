"""Integration tests for artifact publishing and artifacts.* rendering inside StepExecution."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.models import StepExecutionContext, StepResult
from dovo.engine.executors.step_executor import StepExecution
from tests.conftest import PublishArtifact

_NOT_PRODUCED = "Artifact 'plan' matched no files at 'PLAN.md' (ARTIFACT_NOT_PRODUCED)."


def _command_step(command: str, **extra: object) -> StepDefinition:
    """Build a command step declaring the 'plan' artifact unless overridden."""
    payload: dict[str, object] = {
        "id": "plan",
        "type": "command",
        "command": command,
        "artifacts": [{"name": "plan", "path": "PLAN.md"}],
        **extra,
    }
    return StepDefinition.model_validate(payload)


def _run(
    step: StepDefinition,
    worktree_path: Path,
    paths: WorkspacePaths,
    repository: ArtifactsRepository,
    *,
    loop_iteration: int | None = None,
) -> StepResult:
    """Execute step in worktree_path as session wf_1 with artifact storage attached."""
    worktree_path.mkdir(parents=True, exist_ok=True)
    return StepExecution(
        StepExecutionContext(
            step=step,
            worktree_path=worktree_path,
            session_id="wf_1",
            artifact_store=ArtifactStore(Artifacts(paths, repository), "wf_1"),
            paths=paths,
            loop_iteration=loop_iteration,
            diagnostics=[],
        )
    ).run()


class StepExecutionArtifactsTests:
    def test_required_artifact_missing_fails_step_with_not_produced_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        result = _run(_command_step("true"), tmp_path / "wt", engine_paths, artifacts_repository)

        assert (result.status, result.error_message) == ("failed", _NOT_PRODUCED)
        assert artifacts_repository.list() == []

    def test_optional_artifact_missing_completes_with_warning(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        step = _command_step("true", artifacts=[{"name": "plan", "path": "PLAN.md", "required": False}])

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "completed"
        assert result.warnings == [f"Failed to publish artifact 'plan': {_NOT_PRODUCED}"]
        assert artifacts_repository.list() == []

    def test_required_artifact_with_continue_policy_is_ignored_and_publishes_nothing(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        step = _command_step("true", on_failure="continue")

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "ignored"
        assert artifacts_repository.list() == []

    def test_retry_republishes_after_artifact_appears_on_second_attempt(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        step = _command_step(
            '[ "$DOVO_STEP_ATTEMPT" != 2 ] || echo plan > PLAN.md',
            on_failure={"action": "retry", "max_retries": 2, "backoff_ms": 0},
        )

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert (result.status, result.attempts) == ("completed", 2)
        assert [(record.revision, record.attempt) for record in artifacts_repository.list()] == [(1, 2)]

    def test_run_shorthand_step_publishes_declared_artifacts(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        step = StepDefinition.model_validate(
            {"id": "plan", "run": "echo plan > PLAN.md", "artifacts": [{"name": "plan", "path": "PLAN.md"}]}
        )

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "completed"
        assert [record.name for record in artifacts_repository.list()] == ["plan"]

    def test_uses_step_publishes_declared_artifacts(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        engine_paths.catalog_steps_dir.mkdir(parents=True, exist_ok=True)
        (engine_paths.catalog_steps_dir / "make-plan.yaml").write_text(
            "id: make-plan\ntype: command\ncommand: echo plan > PLAN.md\n", encoding="utf-8"
        )
        step = StepDefinition.model_validate(
            {"id": "plan", "uses": "make-plan", "artifacts": [{"name": "plan", "path": "PLAN.md"}]}
        )

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "completed"
        assert [record.name for record in artifacts_repository.list()] == ["plan"]

    def test_failing_assertion_stops_before_artifact_publishing(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        step = _command_step("echo plan > PLAN.md", assert_={"file_exists": "missing.bin"})

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "failed"
        assert result.error_message is not None
        assert result.error_message.startswith("Step 'plan' failed assertion checks:")
        assert artifacts_repository.list() == []


class StepExecutionArtifactReferencesTests:
    def test_command_renders_latest_revision_number_and_content(
        self,
        tmp_path: Path,
        engine_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
        publish_artifact: PublishArtifact,
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "first"})
        publish_artifact("plan", {"PLAN.md": "second"})
        step = StepDefinition.model_validate(
            {
                "id": "show",
                "type": "command",
                "command": 'echo "{{ artifacts.plan.revision }}:${{ artifacts.plan.content }}"',
            }
        )

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.stdout == "2:second\n"

    def test_content_unavailable_fails_step_with_classified_message(
        self,
        tmp_path: Path,
        engine_paths: WorkspacePaths,
        artifacts_repository: ArtifactsRepository,
        publish_artifact: PublishArtifact,
    ) -> None:
        publish_artifact("plan", {"a.md": "a", "b.md": "b"})
        worktree_path = tmp_path / "wt"
        step = StepDefinition.model_validate(
            {"id": "show", "type": "command", "command": "touch ran.marker; echo {{ artifacts.plan.content }}"}
        )

        result = _run(step, worktree_path, engine_paths, artifacts_repository)

        assert result.status == "failed"
        assert result.error_message == (
            "Artifact 'plan' cannot be inlined (ARTIFACT_CONTENT_UNAVAILABLE): "
            "artifact has 2 files, content needs exactly one"
        )
        assert not (worktree_path / "ran.marker").exists()

    def test_later_run_sees_revision_published_by_earlier_run(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        worktree_path = tmp_path / "wt"
        publisher = _command_step("echo plan > PLAN.md")
        renderer = StepDefinition.model_validate(
            {"id": "show", "type": "command", "command": "echo {{ artifacts.plan.revision }}"}
        )

        printed: list[str] = []
        for _ in range(2):
            _run(publisher, worktree_path, engine_paths, artifacts_repository)
            printed.append(_run(renderer, worktree_path, engine_paths, artifacts_repository).stdout)

        assert printed == ["1\n", "2\n"]
