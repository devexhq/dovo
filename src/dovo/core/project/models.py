"""Models for stable project identities."""

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dovo.common.models import BaseResult

PROJECT_ID_REGEX = r"^[a-z0-9][a-z0-9-_]{2,62}$"


class ProjectIdentity(BaseModel):
    """Stable project identity declared in <repo>/.dovo/project.json."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(..., pattern=PROJECT_ID_REGEX, description="Unique project slug.")
    display_name: str | None = Field(default=None, min_length=1, description="Optional display name.")
    created_at: datetime = Field(..., description="UTC creation timestamp.")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        """Reject display names that contain no non-whitespace characters."""
        if value is not None and not value.strip():
            raise ValueError("display_name must contain a non-whitespace character")

        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        """Reject timestamps whose UTC offset is not zero."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be a UTC timestamp")

        return value


class ProjectIdentityErrorType(StrEnum):
    """Classify project identity persistence failures."""

    NOT_FOUND = "NOT_FOUND"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    LOCKED = "LOCKED"
    WRITE_FAILED = "WRITE_FAILED"
    UNREADABLE = "UNREADABLE"


class ProjectIdentityError(BaseModel):
    """Describe one project identity persistence failure."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error_type: ProjectIdentityErrorType
    message: str
    path: str


class ProjectIdentityLoadStatus(StrEnum):
    """Classify project identity loading outcomes."""

    OK = "ok"
    NOT_FOUND = "not_found"
    INVALID_SCHEMA = "invalid_schema"
    UNREADABLE = "unreadable"


class ProjectIdentityLoadResult(BaseResult):
    """Non-raising result of loading a project identity."""

    status: ProjectIdentityLoadStatus
    path: Path
    identity: ProjectIdentity | None = None
    error: ProjectIdentityError | None = None

    @property
    def ok(self) -> bool:
        """Return True when a project identity was loaded."""
        return self.status == ProjectIdentityLoadStatus.OK


class ProjectIdentitySaveStatus(StrEnum):
    """Classify project identity persistence outcomes."""

    OK = "ok"
    LOCKED = "locked"
    WRITE_FAILED = "write_failed"


class ProjectIdentitySaveResult(BaseResult):
    """Non-raising result of saving a project identity."""

    status: ProjectIdentitySaveStatus
    path: Path
    error: ProjectIdentityError | None = None

    @property
    def ok(self) -> bool:
        """Return True when a project identity was saved."""
        return self.status == ProjectIdentitySaveStatus.OK


class ProjectIdentityProvisionStatus(StrEnum):
    """Classify outcomes of provisioning a project identity for `dovo init`."""

    CREATED = "created"
    PRESERVED = "preserved"
    OVERWRITTEN = "overwritten"
    INVALID_ID = "invalid_id"
    FAILED = "failed"


class ProjectIdentityProvisionResult(BaseResult):
    """Non-raising result of creating, preserving, or overwriting a project identity."""

    status: ProjectIdentityProvisionStatus
    path: Path
    identity: ProjectIdentity | None = None

    @property
    def ok(self) -> bool:
        """Return True when a valid identity is present on disk after provisioning."""
        return self.status in (
            ProjectIdentityProvisionStatus.CREATED,
            ProjectIdentityProvisionStatus.PRESERVED,
            ProjectIdentityProvisionStatus.OVERWRITTEN,
        )
