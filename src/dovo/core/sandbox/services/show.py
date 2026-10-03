"""Sandbox detail inspection service."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import (
    SandboxesRepository,
    SandboxStatus,
)
from dovo.core.sandbox.models import (
    SandboxShowResult,
    SandboxShowStatus,
)


def collect_sandbox_show(
    paths: WorkspacePaths,
    db: SandboxesRepository,
    sandbox_id: str,
) -> SandboxShowResult:
    """Look up one sandbox, and reconcile a stale active row.

    Args:
        paths: Resolved command-invocation workspace paths.
        db: SandboxesRepository instance.
        sandbox_id: Sandbox primary key to show.

    Returns:
        Structured show result. Does not print or exit.
    """
    if paths.project_id is None:
        return SandboxShowResult(
            status=SandboxShowStatus.NOT_INITIALIZED,
            errors=["Workspace is not initialized."],
            fixes=["Run `dovo init` to initialize this workspace."],
        )

    row = db.get(sandbox_id)
    if row is None:
        return SandboxShowResult(
            status=SandboxShowStatus.NOT_FOUND,
            errors=[f"Sandbox '{sandbox_id}' not found."],
            fixes=["Run `dovo sandbox list` to see known sandboxes"],
        )

    reconciled = False
    if row.status is SandboxStatus.ACTIVE and not Path(row.sandbox_path).is_dir():
        updated = db.update_status(row.id, SandboxStatus.CLEANED)
        if updated is not None:
            row = updated
        else:
            row = row.model_copy(update={"status": SandboxStatus.CLEANED})
        reconciled = True

    disk_present = Path(row.sandbox_path).exists()
    return SandboxShowResult(
        status=SandboxShowStatus.OK,
        sandbox=row,
        disk_present=disk_present,
        reconciled=reconciled,
    )
