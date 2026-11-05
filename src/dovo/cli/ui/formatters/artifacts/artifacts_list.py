"""ComponentFormatter for ArtifactsListResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactRowView, ArtifactsListView
from dovo.cli.ui.formatters.artifacts.common import build_artifacts_table, format_size_bytes
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.header import header_fields
from dovo.core.artifacts.models import ArtifactsListResult
from dovo.core.db.models import ArtifactRecord


def _to_row(record: ArtifactRecord) -> ArtifactRowView:
    """Derive an ArtifactRowView from one ArtifactRecord."""
    return ArtifactRowView(
        name=record.name,
        session_id=record.session_id,
        revision=record.revision,
        digest=record.digest,
        digest_display=record.digest[:8] if record.digest else "-",
        step_id=record.step_id,
        step_display=record.step_id or "-",
        attempt=record.attempt,
        loop_iteration=record.loop_iteration,
        iteration_display="-" if record.loop_iteration is None else str(record.loop_iteration),
        file_count=record.file_count,
        size_bytes=record.size_bytes,
        size_display=format_size_bytes(record.size_bytes),
        expires_at=record.expires_at,
    )


class ArtifactsListFormatter(ComponentFormatter[ArtifactsListResult, ArtifactsListView]):
    """Formatter for `dovo artifacts list` command results."""

    event_type: ClassVar[str] = "artifacts.list"

    def transform(self, data: ArtifactsListResult) -> ArtifactsListView:
        """Derive the presentation-ready view: the result header, then every artifact row."""
        return ArtifactsListView(**header_fields(data), artifacts=[_to_row(record) for record in data.artifacts])

    def to_rich(self, data: ArtifactsListResult) -> RenderableType:
        """Render the full artifacts table or empty state."""
        view = self.transform(data)
        if not view.artifacts:
            return Text("No artifacts found.")
        return build_artifacts_table(view.artifacts)
