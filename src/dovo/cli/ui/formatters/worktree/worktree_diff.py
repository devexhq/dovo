"""ComponentFormatter for WorktreeDiffResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.syntax import Syntax
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.core.worktree.models import WorktreeDiffResult


class WorktreeDiffFormatter(ComponentFormatter[WorktreeDiffResult]):
    """Formatter for worktree diff command results."""

    def to_rich(self, data: WorktreeDiffResult) -> RenderableType:
        """Render the error panel, the no-changes line, the diffstat or the unified diff, followed by diagnostic_lines(data.diagnostics)."""
        renderable: RenderableType
        if not data.ok:
            renderable = build_error_panel(
                "Worktree Diff Failed",
                data.errors,
                "Failed to generate diff.",
                remediations=data.remediations,
            )
        elif not data.files_changed:
            renderable = Text(f"Worktree '{data.worktree_id}' has no changes compared to base commit.")
        elif data.stat_text:
            renderable = Text(data.stat_text.strip())
        else:
            renderable = Syntax(data.diff_text.strip(), "diff", word_wrap=True)
        return with_diagnostics(renderable, data.diagnostics)
