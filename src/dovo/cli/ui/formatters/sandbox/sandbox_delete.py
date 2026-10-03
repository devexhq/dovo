"""ComponentFormatter for SandboxDeleteResult."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.sandbox.models import SandboxDeleteResult, SandboxDeleteStatus


def _format_delete_error_panel(data: SandboxDeleteResult) -> Panel:
    """Format error panel for non-ok sandbox delete results."""
    if data.status == SandboxDeleteStatus.NOT_INITIALIZED:
        fixes = data.fixes or ["Run `dovo init` to create `.dovo/config.json`"]
        return build_error_panel(
            "Dovo Not Initialized",
            data.errors,
            "Dovo workspace is not initialized.",
            fixes,
        )
    if data.status == SandboxDeleteStatus.NOT_FOUND:
        fixes = data.fixes or ["Run `dovo sandbox list` to see known sandboxes"]
        return build_error_panel(
            "Sandbox Not Found",
            data.errors,
            f"Sandbox '{data.sandbox_id}' not found.",
            fixes,
        )
    return build_error_panel(
        "Sandbox Delete Failed",
        data.errors,
        "Sandbox delete failed.",
        data.fixes,
    )


class SandboxDeleteFormatter(ComponentFormatter[SandboxDeleteResult]):
    """Formatter for sandbox delete command results."""

    def to_rich(self, data: SandboxDeleteResult) -> Any:
        """Render sandbox delete success, aborted, or error state."""
        if data.status == SandboxDeleteStatus.ALREADY_CLEANED:
            return Text(f"Sandbox '{data.sandbox_id}' is already cleaned; nothing to remove.")
        if data.status == SandboxDeleteStatus.ABORTED:
            return Text("Aborted.")
        if data.status == SandboxDeleteStatus.DELETED or data.deleted:
            return Text(f"Sandbox deleted: {data.sandbox_id}", style="green")
        return _format_delete_error_panel(data)
