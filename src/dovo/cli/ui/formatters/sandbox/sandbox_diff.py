"""ComponentFormatter for SandboxDiffResult."""

from __future__ import annotations

from typing import Any

from rich.syntax import Syntax
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.sandbox.models import SandboxDiffResult, SandboxDiffStatus


class SandboxDiffFormatter(ComponentFormatter[SandboxDiffResult]):
    """Formatter for sandbox diff command results."""

    def to_rich(self, data: SandboxDiffResult) -> Any:
        """Render sandbox diff text, stat summary, or error panel."""
        if data.status == SandboxDiffStatus.EMPTY_DIFF:
            return Text(f"Sandbox '{data.sandbox_id}' has no changes compared to base commit.")
        if not data.ok:
            return build_error_panel(
                "Sandbox Diff Failed",
                data.errors,
                "Failed to generate diff.",
                data.fixes,
            )

        if data.stat_text:
            return Text(data.stat_text.strip())
        return Syntax(data.diff_text.strip(), "diff", word_wrap=True)
