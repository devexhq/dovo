"""ComponentFormatter for SandboxListResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.sandbox.common import build_sandbox_table
from dovo.common.types import ComponentFormatter
from dovo.core.sandbox.models import SandboxListResult


class SandboxListFormatter(ComponentFormatter[SandboxListResult]):
    """Formatter for sandbox list command results."""

    def to_rich(self, data: SandboxListResult) -> Any:
        """Render sandbox summary list table or empty state."""
        if not data.ok:
            fixes = data.fixes or ["Run `dovo init` to create `.dovo/config.json`"]
            return build_error_panel(
                "Dovo Not Initialized",
                data.errors,
                "Dovo workspace is not initialized.",
                fixes,
            )

        if not data.sandboxes:
            return Text("No sandboxes found.")

        return build_sandbox_table(data.sandboxes)
