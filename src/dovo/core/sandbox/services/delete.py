"""Collection service for sandbox deletion."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SandboxesRepository, SandboxStatus
from dovo.core.sandbox.models import SandboxDeleteResult, SandboxDeleteStatus


def collect_sandbox_delete(
    paths: WorkspacePaths,
    db: SandboxesRepository,
    *,
    sandbox_id: str,
) -> SandboxDeleteResult:
    """Look up one sandbox for delete (no mutation)."""
    if paths.project_id is None:
        return SandboxDeleteResult(
            status=SandboxDeleteStatus.NOT_INITIALIZED,
            sandbox_id=sandbox_id,
            errors=["Workspace is not initialized."],
            fixes=["Run `dovo init` to initialize this workspace."],
        )

    row = db.get(sandbox_id)
    if row is None:
        return SandboxDeleteResult(
            status=SandboxDeleteStatus.NOT_FOUND,
            sandbox_id=sandbox_id,
            errors=[f"Sandbox '{sandbox_id}' not found."],
            fixes=["Run `dovo sandbox list` to see known sandboxes"],
        )

    if row.status is SandboxStatus.CLEANED:
        return SandboxDeleteResult(
            status=SandboxDeleteStatus.ALREADY_CLEANED,
            sandbox_id=sandbox_id,
            sandbox=row,
        )

    return SandboxDeleteResult(
        status=SandboxDeleteStatus.READY,
        sandbox_id=sandbox_id,
        sandbox=row,
    )
