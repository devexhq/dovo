"""Models for stable project identities."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ProjectIdentityOutcome(StrEnum):
    """What provisioning did to `.dovo/project.json`."""

    CREATED = "created"
    PRESERVED = "preserved"
    OVERWRITTEN = "overwritten"


@dataclass(frozen=True, slots=True)
class ProvisionedProjectIdentity:
    """The identity on disk after provisioning, and how it got there."""

    identity: ProjectIdentity
    path: Path
    outcome: ProjectIdentityOutcome
    warning: str | None = None
