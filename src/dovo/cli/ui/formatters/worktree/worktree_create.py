"""ComponentFormatter for WorktreeCreateResult."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.common.utils import display_path
from dovo.core.worktree.models import WorktreeCreateResult


class WorktreeCreateFormatter(ComponentFormatter[WorktreeCreateResult]):
    """Formatter for worktree creation command results."""

    def to_rich(self, data: WorktreeCreateResult) -> RenderableType:
        """Render worktree creation confirmation or failure panel, followed by diagnostic_lines(data.diagnostics)."""
        if data.ok and data.session is not None:
            root = Path.cwd().resolve()
            path_label = display_path(data.session.worktree_path, root)

            renderables: list[RenderableType] = [
                Text(f"Worktree created: {data.session.session_id}", style="green"),
                Text(f"   Branch: {data.session.target_branch}"),
                Text(f"   Path: {path_label}"),
            ]
            for warning in data.warnings:
                renderables.append(Text(f"   • {warning}", style="dim"))
            return with_diagnostics(Group(*renderables), data.diagnostics)

        panel = build_error_panel(
            "Worktree Create Failed",
            data.errors,
            "Worktree creation failed.",
            remediations=data.remediations,
        )
        return with_diagnostics(panel, data.diagnostics)
