"""Outcome models and DTOs for session artifact publishing, listing, downloading, and pruning."""

from __future__ import annotations

from enum import StrEnum

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


class ArtifactUploadStatus(StrEnum):
    """Classified outcome for publishing an artifact bundle."""

    OK = "ok"
    NO_MATCHING_FILES = "no_matching_files"
    ERROR = "error"


class ArtifactUploadResult(BaseResult):
    """Result of publishing an artifact bundle from a worktree into persistent storage."""

    status: ArtifactUploadStatus
    name: str = ""
    session_id: str = ""
    revision: int = 0
    digest: str | None = None
    size_bytes: int = 0
    file_count: int = 0
    expires_at: str | None = None

    @property
    def ok(self) -> bool:
        """True when the artifact bundle was published."""
        return self.status == ArtifactUploadStatus.OK


class ArtifactDownloadStatus(StrEnum):
    """Classified outcome for downloading an artifact bundle."""

    OK = "ok"
    NOT_FOUND = "not_found"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    ERROR = "error"


class ArtifactDownloadResult(BaseResult):
    """Result of downloading and verifying an artifact bundle."""

    status: ArtifactDownloadStatus
    session_id: str = ""
    name: str = ""
    dest: str = ""
    revision: int = 0
    file_count: int = 0

    @property
    def ok(self) -> bool:
        """True when the artifact bundle was extracted and checksum-verified."""
        return self.status == ArtifactDownloadStatus.OK


class ArtifactReferencesStatus(StrEnum):
    """Classified outcome for resolving artifact placeholder values."""

    OK = "ok"
    CONTENT_UNAVAILABLE = "content_unavailable"


class ArtifactReferencesResult(BaseResult):
    """Resolved {artifact name: {field: value}} values for the latest revision of each requested artifact."""

    status: ArtifactReferencesStatus = ArtifactReferencesStatus.OK
    references: dict[str, dict[str, str]] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when every requested field was resolved (unknown artifacts are simply absent)."""
        return self.status == ArtifactReferencesStatus.OK and not self.errors


class ArtifactsListStatus(StrEnum):
    """Classified outcome for listing artifacts."""

    OK = "ok"


class ArtifactsListResult(BaseResult):
    """Result of listing artifacts for the current project."""

    status: ArtifactsListStatus = ArtifactsListStatus.OK
    artifacts: list[ArtifactRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when listing succeeded (including an empty result)."""
        return self.status == ArtifactsListStatus.OK and not self.errors


class ArtifactsPruneStatus(StrEnum):
    """Classified outcome for pruning expired artifacts."""

    OK = "ok"
    DISABLED = "disabled"
    LOCKED = "locked"


class PrunedArtifact(BaseModel):
    """One artifact revision processed during a prune pass."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    name: str
    revision: int
    pruned: bool
    error: str | None = None


class ArtifactsPruneResult(BaseResult):
    """Result of pruning expired artifacts."""

    status: ArtifactsPruneStatus = ArtifactsPruneStatus.OK
    dry_run: bool = False
    force: bool = False
    items: list[PrunedArtifact] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when prune completed (including a disabled no-op) without failures."""
        return self.status != ArtifactsPruneStatus.LOCKED and not self.errors
