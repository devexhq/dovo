"""Status domain facade."""

from __future__ import annotations

from worktree.common.filesystem import WorkspacePaths
from worktree.core.status.models import WorktreeStatusResult


class Status:
    """Unified entrypoint for workspace status and runtime health collection."""

    def __init__(self, paths: WorkspacePaths) -> None:
        """Bind this Status instance to a resolved WorkspacePaths snapshot."""
        self.paths = paths

    def collect(self) -> WorktreeStatusResult:
        """Collect and return full workspace health and telemetry status."""
        from worktree.core.status.services.collector import collect_status

        return collect_status(self.paths)

    @classmethod
    def collect_at(cls, paths: WorkspacePaths) -> WorktreeStatusResult:
        """Helper to collect workspace status for a resolved WorkspacePaths snapshot."""
        return cls(paths).collect()
