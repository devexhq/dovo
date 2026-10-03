"""ComponentFormatter for SandboxApplyResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.sandbox.models import SandboxApplyResult, SandboxApplyStrategy


def _format_apply_success(data: SandboxApplyResult) -> Group:
    """Render success block for sandbox apply."""
    strategy_label = data.strategy.value
    renderables: list[Any] = [Text(f"Applied sandbox {data.sandbox_id} to workspace ({strategy_label})", style="green")]

    if data.strategy == SandboxApplyStrategy.SQUASH and data.commit_sha:
        renderables.append(Text(f"• Commit: {data.commit_sha}", style="dim"))
    elif data.touched_files:
        files_count = len(data.touched_files)
        files_text = f"{files_count} file changed" if files_count == 1 else f"{files_count} files changed"
        renderables.append(Text(f"• {files_text}", style="dim"))

    renderables.append(Text("• Status updated: merged", style="dim"))

    if data.cleaned_up:
        renderables.append(Text("• Sandbox worktree and branch deleted", style="dim"))

    for warning in data.warnings:
        renderables.append(Text(f"• {warning}", style="dim"))

    return Group(*renderables)


class SandboxApplyFormatter(ComponentFormatter[SandboxApplyResult]):
    """Formatter for sandbox apply command results."""

    def to_rich(self, data: SandboxApplyResult) -> Any:
        """Render sandbox apply success summary or failure panel."""
        if data.ok:
            return _format_apply_success(data)

        return build_error_panel(
            "Sandbox Apply Failed",
            data.errors,
            "Sandbox apply failed.",
            data.fixes,
        )
