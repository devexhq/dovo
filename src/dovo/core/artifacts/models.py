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


class ArtifactManifest(BaseModel):
    """Metadata written as manifest.json alongside a published artifact's files."""

    model_config = {"extra": "forbid", "strict": True}

    name: str
    session_id: str
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
    """Result of publishing an artifact bundle from a sandbox into persistent storage."""

    status: ArtifactUploadStatus
    name: str = ""
    session_id: str = ""
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
    file_count: int = 0

    @property
    def ok(self) -> bool:
        """True when the artifact bundle was extracted and checksum-verified."""
        return self.status == ArtifactDownloadStatus.OK


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
    """One artifact processed during a prune pass."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    name: str
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
