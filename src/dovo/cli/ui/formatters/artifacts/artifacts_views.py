"""Presentation view models for artifacts formatters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.core.artifacts.models import ArtifactsPruneStatus, PrunedArtifact


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


class ArtifactsListView(BaseModel):
    """Presentation view of `dovo artifacts list` results."""

    model_config = {"extra": "forbid", "strict": True}

    artifacts: list[ArtifactRowView] = Field(default_factory=list)


class ArtifactsPruneView(BaseModel):
    """Presentation view of an artifacts prune pass."""

    model_config = {"extra": "forbid", "strict": True}

    status: ArtifactsPruneStatus
    dry_run: bool
    force: bool
    items: list[PrunedArtifact] = Field(default_factory=list)
    pruned_count: int = 0
    failed_count: int = 0
