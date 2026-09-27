"""ComponentFormatter for ArtifactsListResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from worktree.cli.ui.formatters.artifacts.artifacts_views import ArtifactRowView, ArtifactsListView
from worktree.cli.ui.formatters.artifacts.common import build_artifacts_table, format_size_bytes
from worktree.common.types import ComponentFormatter
from worktree.core.artifacts.models import ArtifactsListResult
from worktree.core.db.models import ArtifactRecord


def _to_row(record: ArtifactRecord) -> ArtifactRowView:
    """Derive an ArtifactRowView from one ArtifactRecord."""
    return ArtifactRowView(
        name=record.name,
        session_id=record.session_id,
        file_count=record.file_count,
        size_bytes=record.size_bytes,
        size_display=format_size_bytes(record.size_bytes),
        expires_at=record.expires_at,
    )


class ArtifactsListFormatter(ComponentFormatter[ArtifactsListResult, ArtifactsListView]):
    """Formatter for `wt artifacts list` command results."""

    def transform(self, data: ArtifactsListResult) -> ArtifactsListView:
        """Derive the presentation-ready view listing every artifact row."""
        return ArtifactsListView(artifacts=[_to_row(record) for record in data.artifacts])

    def to_json_serializable(self, data: ArtifactsListResult) -> dict[str, Any]:
        """Emit status plus every artifact row as its ArtifactRowView wire payload."""
        view = self.transform(data)
        return {
            "status": data.status.value,
            "artifacts": [row.model_dump(mode="json") for row in view.artifacts],
            "errors": list(data.errors),
            "warnings": list(data.warnings),
            "fixes": list(data.fixes),
        }

    def to_rich(self, data: ArtifactsListResult) -> Any:
        """Render the full artifacts table or empty state."""
        view = self.transform(data)
        if not view.artifacts:
            return Text("No artifacts found.")
        return build_artifacts_table(view.artifacts)
