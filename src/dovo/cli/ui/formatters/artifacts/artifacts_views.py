"""Presentation view models for artifacts formatters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.cli.ui.formatters.header import ResultHeaderView
from dovo.core.artifacts.models import PrunedArtifact


class ArtifactRowView(BaseModel):
    """Presentation view of one artifact revision row for `dovo artifacts list`."""

    model_config = {"extra": "forbid", "strict": True}

    name: str
    session_id: str
    revision: int
    digest: str | None = None
    digest_display: str
    step_id: str | None = None
    step_display: str
    attempt: int | None = None
    loop_iteration: int | None = None
    iteration_display: str
    file_count: int
    size_bytes: int
    size_display: str
    expires_at: str | None = None


class ArtifactsListView(ResultHeaderView):
    """Presentation view of `dovo artifacts list` results."""

    artifacts: list[ArtifactRowView] = Field(default_factory=list)


class ArtifactsPruneView(ResultHeaderView):
    """Presentation view of an artifacts prune pass."""

    disabled: bool
    dry_run: bool
    force: bool
    items: list[PrunedArtifact] = Field(default_factory=list)
    pruned_count: int = 0
    failed_count: int = 0


class ArtifactDownloadView(ResultHeaderView):
    """Presentation view of `dovo artifacts download`: the header, then the downloaded revision."""

    session_id: str = ""
    name: str = ""
    dest: str = ""
    revision: int = 0
    file_count: int = 0
