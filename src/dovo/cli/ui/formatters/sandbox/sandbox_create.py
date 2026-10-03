"""ComponentFormatter for SandboxCreateResult."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.common.utils import display_path
from dovo.core.sandbox.models import SandboxCreateResult


class SandboxCreateFormatter(ComponentFormatter[SandboxCreateResult]):
    """Formatter for sandbox creation command results."""

    def to_rich(self, data: SandboxCreateResult) -> Any:
        """Render sandbox creation confirmation or failure panel."""
        if data.ok and data.session is not None:
            root = Path.cwd().resolve()
            path_label = display_path(data.session.sandbox_path, root)

            renderables: list[Any] = [
                Text(f"Sandbox created: {data.session.session_id}", style="green"),
                Text(f"   Branch: {data.session.target_branch}"),
                Text(f"   Path: {path_label}"),
            ]
            for warning in data.warnings:
                renderables.append(Text(f"   • {warning}", style="dim"))
            return Group(*renderables)

        return build_error_panel(
            "Sandbox Create Failed",
            data.errors,
            "Sandbox creation failed.",
            data.fixes,
        )
