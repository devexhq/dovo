"""Integration tests for artifact publishing and artifacts.* rendering inside StepExecution."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors.models import StepExecutionContext, StepResult
from dovo.engine.executors.step_executor import StepExecution

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
            artifacts_dir=paths.artifacts_dir,
            artifacts_db=repository,
            paths=paths,
            loop_iteration=loop_iteration,
        )
    ).run()


def _publish_plan(
    paths: WorkspacePaths, repository: ArtifactsRepository, tmp_path: Path, files: dict[str, str]
) -> None:
    """Publish files as the next revision of 'plan' in session wf_1."""
    source = tmp_path / f"source-{len(repository.list())}"
    for relative_path, content in files.items():
        (source / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (source / relative_path).write_text(content, encoding="utf-8")
    result = publish_artifact(
        source,
        paths.artifacts_dir,
        repository,
        session_id="wf_1",
        name="plan",
        path_glob="*",
        retention_days=None,
        provenance=ArtifactProvenance(),
    )
    assert result.ok


class StepExecutionArtifactsTests:
    """[tier-1/integration] StepExecution.run: required/optional publishing of declared artifacts."""

    def test_required_artifact_missing_fails_step_with_not_produced_message(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: command step exiting 0 with required artifact 'PLAN.md' absent -> failed with the ARTIFACT_NOT_PRODUCED message and no row."""
        result = _run(_command_step("true"), tmp_path / "wt", engine_paths, artifacts_repository)

        assert (result.status, result.error_message) == ("failed", _NOT_PRODUCED)
        assert artifacts_repository.list() == []

    def test_optional_artifact_missing_completes_with_warning(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: required=false and PLAN.md absent -> completed, StepResult.warnings hold the 'Failed to publish artifact' text, no row."""
        step = _command_step("true", artifacts=[{"name": "plan", "path": "PLAN.md", "required": False}])

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "completed"
        assert result.warnings == [f"Failed to publish artifact 'plan': {_NOT_PRODUCED}"]
        assert artifacts_repository.list() == []

    def test_required_artifact_with_continue_policy_is_ignored_and_publishes_nothing(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: on_failure=continue and a missing required artifact -> status 'ignored' and no artifact rows."""
        step = _command_step("true", on_failure="continue")

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "ignored"
        assert artifacts_repository.list() == []

    def test_retry_republishes_after_artifact_appears_on_second_attempt(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: retry(max_retries=2) with PLAN.md written only on DOVO_STEP_ATTEMPT=2 -> completed, attempts 2, one row revision 1 with attempt 2."""
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
        """[tier-1/integration] StepExecution.run: a run: step declaring artifacts publishes them (the shorthand expansion keeps artifacts)."""
        step = StepDefinition.model_validate(
            {"id": "plan", "run": "echo plan > PLAN.md", "artifacts": [{"name": "plan", "path": "PLAN.md"}]}
        )

        result = _run(step, tmp_path / "wt", engine_paths, artifacts_repository)

        assert result.status == "completed"
        assert [record.name for record in artifacts_repository.list()] == ["plan"]

    def test_uses_step_publishes_declared_artifacts(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: a uses: step carrying artifacts: [{name, path}] publishes the artifact (merge_uses_step keeps artifacts)."""
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


class StepExecutionArtifactReferencesTests:
    """[tier-1/integration] StepExecution.run: artifacts.* placeholder rendering."""

    def test_command_renders_latest_revision_number_and_content(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: command echoing revision and content with plan at revision 2 -> stdout '2:' followed by the revision-2 text."""
        _publish_plan(engine_paths, artifacts_repository, tmp_path, {"PLAN.md": "first"})
        _publish_plan(engine_paths, artifacts_repository, tmp_path, {"PLAN.md": "second"})
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
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: content requested for a two-file artifact -> failed with the ARTIFACT_CONTENT_UNAVAILABLE message and the command never ran."""
        _publish_plan(engine_paths, artifacts_repository, tmp_path, {"a.md": "a", "b.md": "b"})
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
        """[tier-1/integration] StepExecution.run twice with the same artifacts_db: a publishing step then a rendering step print revision 1, then 2 after a second publish."""
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
