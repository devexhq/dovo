"""In-process handlers and registry for `type: internal` step commands (`artifacts.upload`, `artifacts.download`)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dovo.core.artifacts.models import ArtifactDownloadStatus, ArtifactUploadStatus
from dovo.core.artifacts.services.download import download_artifact
from dovo.core.artifacts.services.upload import publish_artifact
from dovo.engine.executors.models import InternalCommandContext, StepDispatchOutcome


def _no_active_session_outcome(command: str) -> StepDispatchOutcome:
    """Build the failed outcome for an internal command with no active session to publish/download against."""
    return StepDispatchOutcome(
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        error_message=f"Internal command '{command}' requires an active session, but none is set.",
    )


def handle_artifacts_upload(ctx: InternalCommandContext) -> StepDispatchOutcome:
    """Read ARTIFACT_NAME/ARTIFACT_PATH/ARTIFACT_RETENTION_DAYS from ctx.env and call publish_artifact."""
    if ctx.artifacts_dir is None or ctx.artifacts_db is None:
        return _no_active_session_outcome("artifacts.upload")

    name = ctx.env.get("ARTIFACT_NAME", "")
    path_glob = ctx.env.get("ARTIFACT_PATH", "")
    if not name or not path_glob:
        return StepDispatchOutcome(
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            error_message="Internal command 'artifacts.upload' requires ARTIFACT_NAME and ARTIFACT_PATH.",
        )

    retention_raw = ctx.env.get("ARTIFACT_RETENTION_DAYS")
    retention_days = int(retention_raw) if retention_raw else None

    result = publish_artifact(
        ctx.sandbox_path,
        ctx.artifacts_dir,
        ctx.artifacts_db,
        session_id=ctx.session_id,
        name=name,
        path_glob=path_glob,
        retention_days=retention_days,
    )
    if result.status == ArtifactUploadStatus.OK:
        return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")
    return StepDispatchOutcome(
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        error_message="; ".join(result.errors) or f"Publishing artifact '{name}' failed.",
    )


def handle_artifacts_download(ctx: InternalCommandContext) -> StepDispatchOutcome:
    """Read ARTIFACT_NAME/ARTIFACT_DEST/optional ARTIFACT_SESSION_ID from ctx.env and call download_artifact."""
    if ctx.artifacts_dir is None or ctx.artifacts_db is None:
        return _no_active_session_outcome("artifacts.download")

    name = ctx.env.get("ARTIFACT_NAME", "")
    dest_raw = ctx.env.get("ARTIFACT_DEST", "")
    if not name or not dest_raw:
        return StepDispatchOutcome(
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            error_message="Internal command 'artifacts.download' requires ARTIFACT_NAME and ARTIFACT_DEST.",
        )

    session_id = ctx.env.get("ARTIFACT_SESSION_ID", ctx.session_id)
    dest_path = Path(dest_raw)
    dest = dest_path if dest_path.is_absolute() else ctx.sandbox_path / dest_path

    result = download_artifact(
        ctx.artifacts_dir,
        ctx.artifacts_db,
        session_id=session_id,
        name=name,
        dest=dest,
    )
    if result.status == ArtifactDownloadStatus.OK:
        return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")
    return StepDispatchOutcome(
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        error_message="; ".join(result.errors) or f"Downloading artifact '{name}' failed.",
    )


INTERNAL_COMMAND_HANDLERS: dict[str, Callable[[InternalCommandContext], StepDispatchOutcome]] = {
    "artifacts.upload": handle_artifacts_upload,
    "artifacts.download": handle_artifacts_download,
}
