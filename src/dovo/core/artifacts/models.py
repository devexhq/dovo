"""Outcome models and DTOs for session artifact publishing, listing, downloading, and pruning."""

from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult
from dovo.core.db.models import ArtifactRecord


class ArtifactManifestFile(BaseModel):
    """One file's identity within a published artifact bundle."""

    model_config = {"extra": "forbid", "strict": True}

    path: str
    sha256: str
    size_bytes: int


class ArtifactProvenance(BaseModel):
    """Which step attempt published an artifact revision; every field is None when not applicable."""

    model_config = {"extra": "forbid", "strict": True}

    step_id: str | None = None
    attempt: int | None = None
    loop_iteration: int | None = None


class ArtifactManifest(BaseModel):
    """Metadata written as manifest.json alongside a published artifact's files."""

    model_config = {"extra": "forbid", "strict": True}

    name: str
    session_id: str
    revision: int = 1
    digest: str | None = None
    step_id: str | None = None
    attempt: int | None = None
    loop_iteration: int | None = None
    created_at: str
    expires_at: str | None = None
    size_bytes: int
    file_count: int
    files: list[ArtifactManifestFile] = Field(default_factory=list)


class ArtifactUploadResult(BaseResult):
    """Result of publishing an artifact bundle from a worktree into persistent storage."""

    name: str = ""
    session_id: str = ""
    revision: int = 0
    digest: str | None = None
    size_bytes: int = 0
    file_count: int = 0
    expires_at: str | None = None

    @property
    def ok(self) -> bool:
        """True when error_code is None: the artifact bundle was published or the unchanged latest revision kept."""
        return self.error_code is None


class ArtifactDownloadResult(BaseResult):
    """Result of downloading and verifying an artifact bundle."""

    session_id: str = ""
    name: str = ""
    dest: str = ""
    revision: int = 0
    file_count: int = 0

    @property
    def ok(self) -> bool:
        """True when error_code is None: the artifact bundle was extracted and checksum-verified."""
        return self.error_code is None


class ArtifactReferencesResult(BaseResult):
    """Resolved {artifact name: {field: value}} values for the latest revision of each requested artifact."""

    references: dict[str, dict[str, str]] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when error_code is None: every requested field was resolved (unknown artifacts are simply absent)."""
        return self.error_code is None


class ArtifactsListResult(BaseResult):
    """Result of listing artifacts for the current project."""

    artifacts: list[ArtifactRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when error_code is None (including an empty listing)."""
        return self.error_code is None


class PrunedArtifact(BaseModel):
    """One artifact revision processed during a prune pass."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    name: str
    revision: int
    pruned: bool
    error: str | None = None


class ArtifactsPruneResult(BaseResult):
    """Result of pruning expired artifacts; disabled is True when pruning was skipped because it is turned off."""

    disabled: bool = False
    dry_run: bool = False
    force: bool = False
    items: list[PrunedArtifact] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when error_code is None, including a disabled no-op and items that failed to prune."""
        return self.error_code is None
