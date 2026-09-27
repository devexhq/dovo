"""Registry mapping internal step command names to in-process handler functions."""

from __future__ import annotations

from collections.abc import Callable

from worktree.core.artifacts.services.internal_commands import handle_artifacts_download, handle_artifacts_upload
from worktree.core.step.models import InternalCommandContext, StepDispatchOutcome

INTERNAL_COMMAND_HANDLERS: dict[str, Callable[[InternalCommandContext], StepDispatchOutcome]] = {
    "artifacts.upload": handle_artifacts_upload,
    "artifacts.download": handle_artifacts_download,
}
