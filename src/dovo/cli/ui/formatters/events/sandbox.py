"""ComponentFormatter for SandboxLifecycleEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import SandboxLifecycleEvent
from dovo.common.types import ComponentFormatter


class SandboxLifecycleFormatter(ComponentFormatter[SandboxLifecycleEvent]):
    """Formatter for sandbox lifecycle notices."""

    def to_rich(self, data: SandboxLifecycleEvent) -> Text:
        """Render sandbox ready or cleanup notice."""
        if data.action == "ready":
            if data.active:
                return Text(f"Sandbox: Active ({data.path})")
            return Text("Sandbox: In-place (workspace)")
        if data.action == "cleanup":
            if data.kept:
                return Text(f"Sandbox: Retained ({data.path})")
            return Text("Sandbox: Cleaned")
        return Text(f"Sandbox: {data.action} ({data.path})")
