"""Publishing of a step's declared `artifacts:` entries from inside the executor attempt loop."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.core.artifacts.models import ArtifactProvenance, ArtifactUploadResult, ArtifactUploadStatus
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition


class ArtifactPublishOutcome(BaseModel):
    """Result of publishing a step's declared artifacts: the first required failure, non-fatal warnings and recorded failures."""

    model_config = {"extra": "forbid", "strict": True}

    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)  # Records for the run's diagnostics only.


def _publish_one(
    spec: ArtifactPublishSpec,
    *,
    step_id: str,
    worktree_path: Path,
    store: ArtifactStore,
    provenance: ArtifactProvenance,
) -> tuple[ArtifactUploadResult, list[DiagnosticRecord]]:
    """Publish one spec; an exception becomes an ERROR result plus the recorded event's record, never raised."""
    try:
        result = store.publish(
            worktree_path,
            name=spec.name,
            path_glob=spec.path,
            retention_days=spec.retention_days,
            provenance=provenance,
        )
    except Exception as exc:
        record = record_exception(
            exc,
            level=DiagnosticLevel.ERROR,
            operation="engine.step",
            context={"step_id": step_id, "path": str(worktree_path)},
        )
        return ArtifactUploadResult(status=ArtifactUploadStatus.ERROR, errors=[str(exc)]), error_diagnostics(record)

    return result, []


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
    diagnostics: list[DiagnosticRecord] = []
    for spec in step.artifacts:
        result, records = _publish_one(
            spec, step_id=step.id, worktree_path=worktree_path, store=store, provenance=provenance
        )
        diagnostics.extend(records)
        if result.ok:
            continue

        failure = _failed_publish(spec, result, warnings, diagnostics)
        if failure is not None:
            return failure

    return ArtifactPublishOutcome(warnings=warnings, diagnostics=diagnostics)


def _failed_publish(
    spec: ArtifactPublishSpec,
    result: ArtifactUploadResult,
    warnings: list[str],
    diagnostics: list[DiagnosticRecord],
) -> ArtifactPublishOutcome | None:
    """Append an optional spec's warning to warnings and return None; return the step-failing outcome for a required spec."""
    detail = "; ".join(result.errors) or "publish failed"
    if not spec.required:
        warnings.append(f"Failed to publish artifact '{spec.name}': {detail}")
        return None

    if result.status == ArtifactUploadStatus.NO_MATCHING_FILES:
        return ArtifactPublishOutcome(error_message=detail, warnings=warnings, diagnostics=diagnostics)

    return ArtifactPublishOutcome(
        error_message=f"Failed to publish artifact '{spec.name}': {detail} (ARTIFACT_PUBLISH_FAILED)",
        warnings=warnings,
        diagnostics=diagnostics,
    )
