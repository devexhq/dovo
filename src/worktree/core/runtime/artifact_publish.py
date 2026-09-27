"""Shared declarative artifact auto-publish helper for top-level and loop sub-steps."""

from __future__ import annotations

from pathlib import Path

from worktree.core.artifacts.services.upload import publish_artifact
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.step.models import StepDefinition


def auto_publish_step_artifacts(
    step: StepDefinition,
    *,
    sandbox_path: Path,
    session_id: str,
    artifacts_dir: Path | None,
    artifacts_db: ArtifactsRepository | None,
) -> list[str]:
    """Publish every artifacts: entry declared on step via the shared publish_artifact service; return warnings, never raise."""
    if not step.artifacts or artifacts_dir is None or artifacts_db is None:
        return []

    warnings: list[str] = []
    for spec in step.artifacts:
        try:
            result = publish_artifact(
                sandbox_path,
                artifacts_dir,
                artifacts_db,
                session_id=session_id,
                name=spec.name,
                path_glob=spec.path,
                retention_days=spec.retention_days,
            )
        except Exception as exc:
            warnings.append(f"Failed to publish artifact '{spec.name}': {exc}")
            continue
        if not result.ok:
            detail = "; ".join(result.errors) or "publish failed"
            warnings.append(f"Failed to publish artifact '{spec.name}': {detail}")

    return warnings
