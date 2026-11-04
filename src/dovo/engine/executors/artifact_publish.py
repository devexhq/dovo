"""Publishing of a step's declared `artifacts:` entries from inside the executor attempt loop."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.core.artifacts.models import ArtifactProvenance, ArtifactUploadResult
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition


class ArtifactPublishOutcome(BaseModel):
    """Result of publishing a step's declared artifacts: the first required failure, non-fatal warnings and the store's records."""

    model_config = {"extra": "forbid", "strict": True}

    error_message: str | None = None
    error_code: ErrorCode | None = None  # the failed publish's code, set with error_message
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)  # Records for the run's diagnostics only.


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
        result = store.publish(
            worktree_path,
            name=spec.name,
            path_glob=spec.path,
            retention_days=spec.retention_days,
            provenance=provenance,
        )
        diagnostics.extend(result.diagnostics)
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
    """Append an optional spec's warning to warnings and return None; return the step-failing outcome, carrying the publish's code, for a required spec."""
    detail = "; ".join(result.errors)
    if not spec.required:
        warnings.append(f"Failed to publish artifact '{spec.name}': {detail}")
        return None

    if result.error_code == ErrorCode.ARTIFACT_FILE_NOT_FOUND:
        return ArtifactPublishOutcome(
            error_message=detail, error_code=result.error_code, warnings=warnings, diagnostics=diagnostics
        )

    return ArtifactPublishOutcome(
        error_message=f"Failed to publish artifact '{spec.name}': {detail}",
        error_code=result.error_code,
        warnings=warnings,
        diagnostics=diagnostics,
    )
