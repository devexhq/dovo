"""ComponentFormatter for ArtifactDownloadResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from worktree.cli.ui.formatters.common import build_error_panel
from worktree.common.types import ComponentFormatter
from worktree.core.artifacts.models import ArtifactDownloadResult, ArtifactDownloadStatus


class ArtifactDownloadFormatter(ComponentFormatter[ArtifactDownloadResult]):
    """Formatter for `wt artifacts download` command results."""

    def to_rich(self, data: ArtifactDownloadResult) -> Any:
        """Render download success or an error panel keyed by status."""
        if data.status == ArtifactDownloadStatus.OK:
            return Text(f"Downloaded artifact '{data.name}' ({data.file_count} files) to '{data.dest}'.", style="green")
        return build_error_panel(
            "Artifact Download Failed",
            data.errors,
            f"Failed to download artifact '{data.name}' for session '{data.session_id}'.",
            data.fixes,
        )
