"""ComponentFormatter for WorktreeDiffResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.syntax import Syntax
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreeDiffView
from dovo.core.worktree.models import WorktreeDiffResult


class WorktreeDiffFormatter(ComponentFormatter[WorktreeDiffResult, WorktreeDiffView]):
    """Formatter for worktree diff command results."""

    def transform(self, data: WorktreeDiffResult) -> WorktreeDiffView:
        """Derive the view: the result header, then the diff, its stat and the changed files."""
        return WorktreeDiffView(
            **header_fields(data),
            worktree_id=data.worktree_id,
            diff_text=data.diff_text,
            stat_text=data.stat_text,
            files_changed=list(data.files_changed),
        )

    def to_rich(self, data: WorktreeDiffResult) -> RenderableType:
        """Render the error panel, the no-changes line, the diffstat or the unified diff, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        renderable: RenderableType
        if not view.ok:
            renderable = build_error_panel(
                "Worktree Diff Failed",
                view.errors,
                "Failed to generate diff.",
                remediations=view.remediations,
            )
        elif not view.files_changed:
            renderable = Text(f"Worktree '{view.worktree_id}' has no changes compared to base commit.")
        elif view.stat_text:
            renderable = Text(view.stat_text.strip())
        else:
            renderable = Syntax(view.diff_text.strip(), "diff", word_wrap=True)
        return with_diagnostics(renderable, view.diagnostics)
