"""Blueprint run result model."""

from __future__ import annotations

from worktree.common.models import BaseResult
from worktree.core.db import RunRecord


class BlueprintRunResult(BaseResult):
    """Unified result for task and blueprint execution."""

    run_record: RunRecord | None = None

    @property
    def ok(self) -> bool:
        """Return True if run completed without fatal errors."""
        return self.run_record is not None and len(self.errors) == 0
