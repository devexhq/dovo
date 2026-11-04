"""ComponentFormatter for WorktreeCreateResult."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreeCreateView
from dovo.common.utils import display_path
from dovo.core.worktree.models import WorktreeCreateResult


class WorktreeCreateFormatter(ComponentFormatter[WorktreeCreateResult, WorktreeCreateView]):
    """Formatter for worktree creation command results."""

    def transform(self, data: WorktreeCreateResult) -> WorktreeCreateView:
        """Derive the view: the result header, then the created worktree session."""
        return WorktreeCreateView(**header_fields(data), session=data.session)

    def to_rich(self, data: WorktreeCreateResult) -> RenderableType:
        """Render worktree creation confirmation or failure panel, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        if view.ok and view.session is not None:
            root = Path.cwd().resolve()
            path_label = display_path(view.session.worktree_path, root)

            renderables: list[RenderableType] = [
                Text(f"Worktree created: {view.session.session_id}", style="green"),
                Text(f"   Branch: {view.session.target_branch}"),
                Text(f"   Path: {path_label}"),
            ]
            for warning in view.warnings:
                renderables.append(Text(f"   • {warning}", style="dim"))
            return with_diagnostics(Group(*renderables), view.diagnostics)

        panel = build_error_panel(
            "Worktree Create Failed",
            view.errors,
            "Worktree creation failed.",
            remediations=view.remediations,
        )
        return with_diagnostics(panel, view.diagnostics)
