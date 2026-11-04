"""In-process handlers and registry for `type: internal` step commands (`artifacts.upload`, `artifacts.download`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.engine.executors.models import InternalCommandContext, StepDispatchOutcome


def _failed_outcome(
    error_code: ErrorCode, message: str, *, diagnostics: Sequence[DiagnosticRecord] = ()
) -> StepDispatchOutcome:
    """Build a failed internal command outcome with exit code 1, its error code, message and diagnostics."""
    return StepDispatchOutcome(
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        error_message=message,
        error_code=error_code,
        diagnostics=list(diagnostics),
    )


def _no_active_session_outcome(command: str) -> StepDispatchOutcome:
    """Build the failed outcome for an internal command with no active session to publish/download against."""
    return _failed_outcome(
        ErrorCode.STEP_SESSION_MISSING,
        f"Internal command '{command}' requires an active session, but none is set.",
    )


def handle_artifacts_upload(ctx: InternalCommandContext) -> StepDispatchOutcome:
    """Read ARTIFACT_NAME/ARTIFACT_PATH/ARTIFACT_RETENTION_DAYS from ctx.env and publish through the artifact store."""
    if ctx.artifact_store is None:
        return _no_active_session_outcome("artifacts.upload")

    name = ctx.env.get("ARTIFACT_NAME", "")
    path_glob = ctx.env.get("ARTIFACT_PATH", "")
    if not name or not path_glob:
        return _failed_outcome(
            ErrorCode.STEP_INPUT_INVALID,
            "Internal command 'artifacts.upload' requires ARTIFACT_NAME and ARTIFACT_PATH.",
        )

    retention_raw = ctx.env.get("ARTIFACT_RETENTION_DAYS")
    retention_days = int(retention_raw) if retention_raw else None

    result = ctx.artifact_store.publish(
        ctx.worktree_path,
        name=name,
        path_glob=path_glob,
        retention_days=retention_days,
        provenance=ArtifactProvenance(step_id=ctx.step_id, attempt=ctx.attempt, loop_iteration=ctx.loop_iteration),
    )
    if result.error_code is None:
        return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")
    return _failed_outcome(
        result.error_code,
        "; ".join(result.errors) or f"Publishing artifact '{name}' failed.",
        diagnostics=result.diagnostics,
    )


def _revision_from_env(env: Mapping[str, str]) -> tuple[int | None, str | None]:
    """Parse optional ARTIFACT_REVISION into (revision, error); (None, None) when unset."""
    raw = env.get("ARTIFACT_REVISION")
    if not raw:
        return None, None
    try:
        revision = int(raw)
    except ValueError:
        revision = 0
    if revision < 1:
        return None, "Internal command 'artifacts.download' requires ARTIFACT_REVISION to be a positive integer."
    return revision, None


def handle_artifacts_download(ctx: InternalCommandContext) -> StepDispatchOutcome:
    """Read ARTIFACT_NAME/ARTIFACT_DEST/optional ARTIFACT_SESSION_ID and ARTIFACT_REVISION from ctx.env and download through the artifact store."""
    if ctx.artifact_store is None:
        return _no_active_session_outcome("artifacts.download")

    name = ctx.env.get("ARTIFACT_NAME", "")
    dest_raw = ctx.env.get("ARTIFACT_DEST", "")
    if not name or not dest_raw:
        return _failed_outcome(
            ErrorCode.STEP_INPUT_INVALID,
            "Internal command 'artifacts.download' requires ARTIFACT_NAME and ARTIFACT_DEST.",
        )

    revision, revision_error = _revision_from_env(ctx.env)
    if revision_error is not None:
        return _failed_outcome(ErrorCode.STEP_INPUT_INVALID, revision_error)

    dest_path = Path(dest_raw)
    dest = dest_path if dest_path.is_absolute() else ctx.worktree_path / dest_path

    result = ctx.artifact_store.download(
        name=name,
        dest=dest,
        revision=revision,
        session_id=ctx.env.get("ARTIFACT_SESSION_ID"),
    )
    if result.error_code is None:
        return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")
    return _failed_outcome(
        result.error_code,
        "; ".join(result.errors) or f"Downloading artifact '{name}' failed.",
    )


INTERNAL_COMMAND_HANDLERS: dict[str, Callable[[InternalCommandContext], StepDispatchOutcome]] = {
    "artifacts.upload": handle_artifacts_upload,
    "artifacts.download": handle_artifacts_download,
}
