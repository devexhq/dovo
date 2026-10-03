"""ComponentFormatter for WorktreeDiffResult."""

from __future__ import annotations

from typing import Any

from rich.syntax import Syntax
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.worktree.models import WorktreeDiffResult, WorktreeDiffStatus


class WorktreeDiffFormatter(ComponentFormatter[WorktreeDiffResult]):
    """Formatter for worktree diff command results."""

    def to_rich(self, data: WorktreeDiffResult) -> Any:
        """Render worktree diff text, stat summary, or error panel."""
        if data.status == WorktreeDiffStatus.EMPTY_DIFF:
            return Text(f"Worktree '{data.worktree_id}' has no changes compared to base commit.")
        if not data.ok:
            return build_error_panel(
                "Worktree Diff Failed",
                data.errors,
                "Failed to generate diff.",
                data.fixes,
            )

        if data.stat_text:
            return Text(data.stat_text.strip())
        return Syntax(data.diff_text.strip(), "diff", word_wrap=True)
