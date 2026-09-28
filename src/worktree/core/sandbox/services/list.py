"""Sandbox listing service."""

from __future__ import annotations

from worktree.common.filesystem import WorkspacePaths
from worktree.core.db import (
    SandboxesRepository,
    SandboxStatus,
)
from worktree.core.sandbox.models import (
    SandboxListResult,
    SandboxListStatus,
)


def collect_sandbox_list(
    paths: WorkspacePaths,
    db: SandboxesRepository,
    status: str | None = None,
) -> SandboxListResult:
    """Reconcile stale active rows and return list data.

    Args:
        paths: Resolved command-invocation workspace paths.
        db: SandboxesRepository instance.
        status: Optional status filter (active, merged, cleaned,
            conflict). Reconciliation always runs on the full row set first.

    Returns:
        Structured list result. Does not print or exit.
    """
    if paths.project_id is None:
        return SandboxListResult(status=SandboxListStatus.NOT_INITIALIZED, sandboxes=[])

    db.reconcile_stale_active()

    status_filter: SandboxStatus | None = None
    if status is not None:
        status_filter = SandboxStatus(status)

    rows = db.list(status=status_filter)
    return SandboxListResult(status=SandboxListStatus.OK, sandboxes=rows)
