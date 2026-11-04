"""Presentation view models for artifacts formatters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.cli.ui.formatters.base import ResultView
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


class ArtifactsListView(ResultView):
    """Presentation view of `dovo artifacts list` results."""

    artifacts: list[ArtifactRowView] = Field(default_factory=list)


class ArtifactsPruneView(ResultView):
    """Presentation view of an artifacts prune pass."""

    disabled: bool
    dry_run: bool
    force: bool
    items: list[PrunedArtifact] = Field(default_factory=list)
    pruned_count: int = 0
    failed_count: int = 0
