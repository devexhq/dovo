"""ComponentFormatter for ArtifactDownloadResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactDownloadView
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.header import header_fields
from dovo.core.artifacts.models import ArtifactDownloadResult


class ArtifactDownloadFormatter(ComponentFormatter[ArtifactDownloadResult, ArtifactDownloadView]):
    """Formatter for `dovo artifacts download` command results."""

    def transform(self, data: ArtifactDownloadResult) -> ArtifactDownloadView:
        """Derive the view: the result header, then the downloaded revision."""
        return ArtifactDownloadView(
            **header_fields(data),
            session_id=data.session_id,
            name=data.name,
            dest=data.dest,
            revision=data.revision,
            file_count=data.file_count,
        )

    def to_rich(self, data: ArtifactDownloadResult) -> RenderableType:
        """Render download success, or an error panel when the download failed."""
        view = self.transform(data)
        if view.ok:
            return Text(
                f"Downloaded artifact '{view.name}' revision {view.revision} ({view.file_count} files) to '{view.dest}'.",
                style="green",
            )
        return build_error_panel(
            "Artifact Download Failed",
            view.errors,
            f"Failed to download artifact '{view.name}' for session '{view.session_id}'.",
            remediations=view.remediations,
        )
