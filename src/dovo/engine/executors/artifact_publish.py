"""Publishing of a step's declared `artifacts:` entries from inside the executor attempt loop."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.core.artifacts.models import ArtifactProvenance, ArtifactUploadResult, ArtifactUploadStatus
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition
from dovo.engine.executors.artifact_store import ArtifactStore


class ArtifactPublishOutcome(BaseModel):
    """Result of publishing a step's declared artifacts: the first required failure, plus non-fatal warnings."""

    model_config = {"extra": "forbid", "strict": True}

    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _publish_one(
    spec: ArtifactPublishSpec,
    *,
    worktree_path: Path,
    store: ArtifactStore,
    provenance: ArtifactProvenance,
) -> ArtifactUploadResult:
    """Publish one spec and return its result; an exception becomes an ERROR result, never raised."""
    try:
        return store.publish(
            worktree_path,
            name=spec.name,
            path_glob=spec.path,
            retention_days=spec.retention_days,
            provenance=provenance,
        )
    except Exception as exc:
        return ArtifactUploadResult(status=ArtifactUploadStatus.ERROR, errors=[str(exc)])


def publish_declared_artifacts(
    step: StepDefinition,
    *,
    worktree_path: Path,
    store: ArtifactStore | None,
    attempt: int,
    loop_iteration: int | None,
) -> ArtifactPublishOutcome:
    """Publish every artifacts: entry of step in order, stopping at the first required failure."""
    if store is None or not step.artifacts:
        return ArtifactPublishOutcome()

    provenance = ArtifactProvenance(step_id=step.id, attempt=attempt, loop_iteration=loop_iteration)
    warnings: list[str] = []
    for spec in step.artifacts:
        result = _publish_one(spec, worktree_path=worktree_path, store=store, provenance=provenance)
        if result.ok:
            continue

        detail = "; ".join(result.errors) or "publish failed"
        if not spec.required:
            warnings.append(f"Failed to publish artifact '{spec.name}': {detail}")
        elif result.status == ArtifactUploadStatus.NO_MATCHING_FILES:
            return ArtifactPublishOutcome(error_message=detail, warnings=warnings)
        else:
            return ArtifactPublishOutcome(
                error_message=f"Failed to publish artifact '{spec.name}': {detail} (ARTIFACT_PUBLISH_FAILED)",
                warnings=warnings,
            )

    return ArtifactPublishOutcome(warnings=warnings)
