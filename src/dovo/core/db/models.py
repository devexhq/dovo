"""Pydantic and SQLModel record models and enums for the database layer."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import String, TypeDecorator, UniqueConstraint
from sqlmodel import Field, SQLModel


class WorktreeStatus(StrEnum):
    """Lifecycle status for a persisted worktree metadata row."""

    ACTIVE = "active"
    MERGED = "merged"
    CLEANED = "cleaned"
    CONFLICT = "conflict"


class SessionStatus(StrEnum):
    """Lifecycle status of a session record."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


def _now_utc_str() -> str:
    """Return the current UTC timestamp formatted as a date-time string."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def parse_timestamp(timestamp_str: str | None) -> datetime | None:
    """Safely parse a timestamp string across supported ISO and SQLite UTC formats."""
    if not timestamp_str or not timestamp_str.strip():
        return None
    cleaned = timestamp_str.strip()
    try:
        parsed = datetime.fromisoformat(cleaned)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    for format_pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, format_pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class PathType(TypeDecorator[Path]):
    """SQLAlchemy type for coercing Path objects to strings and back."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Path | str | None, dialect: Any) -> str | None:
        """Coerce incoming Path or str object to string for SQLite storage."""
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> Path | None:
        """Coerce retrieved database string value back into a Path instance."""
        if value is None:
            return None
        return Path(value)


class WorktreeStatusType(TypeDecorator[WorktreeStatus]):
    """SQLAlchemy type for coercing WorktreeStatus enums to strings and back."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: WorktreeStatus | str | None, dialect: Any) -> str | None:
        """Coerce incoming WorktreeStatus or str to string for SQLite storage."""
        if value is None:
            return None
        return value.value if isinstance(value, WorktreeStatus) else str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> WorktreeStatus | None:
        """Coerce retrieved database string value back into a WorktreeStatus instance."""
        if value is None:
            return None
        return WorktreeStatus(value)


class SessionStatusType(TypeDecorator[SessionStatus]):
    """SQLAlchemy type for coercing SessionStatus enums to strings and back."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: SessionStatus | str | None, dialect: Any) -> str | None:
        """Coerce incoming SessionStatus or str to string for SQLite storage."""
        if value is None:
            return None
        return value.value if isinstance(value, SessionStatus) else str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> SessionStatus | None:
        """Coerce retrieved database string value back into a SessionStatus instance."""
        if value is None:
            return None
        return SessionStatus(value)


class WorktreeRecord(SQLModel, table=True):
    """Row shape for the centralized `worktrees` table."""

    __tablename__: ClassVar[str] = "worktrees"  # pyright: ignore[reportIncompatibleVariableOverride]
    model_config = {"extra": "forbid"}

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    name: str | None = Field(default=None)
    branch_name: str
    base_commit: str
    worktree_path: Path = Field(sa_type=PathType, unique=True)
    status: WorktreeStatus = Field(default=WorktreeStatus.ACTIVE, sa_type=WorktreeStatusType, index=True)
    created_at: str = Field(default_factory=_now_utc_str)
    updated_at: str = Field(default_factory=_now_utc_str)

    def __init__(self, **data: Any) -> None:
        """Initialize WorktreeRecord, coercing string paths to Path instances."""
        if "worktree_path" in data and isinstance(data["worktree_path"], str):
            data["worktree_path"] = Path(data["worktree_path"])
        super().__init__(**data)


class SessionRecord(SQLModel, table=True):
    """Row shape for the centralized `sessions` table."""

    __tablename__: ClassVar[str] = "sessions"  # pyright: ignore[reportIncompatibleVariableOverride]
    model_config = {"extra": "forbid"}

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    session_id: str = Field(unique=True)
    blueprint_key: str
    blueprint_name: str
    branch_name: str = Field(default="")
    status: SessionStatus = Field(default=SessionStatus.RUNNING, sa_type=SessionStatusType, index=True)
    pid: int | None = Field(default=None)
    started_at: str = Field(default_factory=_now_utc_str, index=True)
    completed_at: str | None = Field(default=None)
    error_message: str | None = Field(default=None)

    execution_state_json: str | None = Field(default=None)
    execution_state_revision: int | None = Field(default=0)
    blueprint_tier: str | None = Field(default=None)
    commit_sha: str | None = Field(default=None)
    use_worktree: bool = Field(default=True)
    keep: bool = Field(default=False)
    agent: str | None = Field(default=None)
    inputs_json: str | None = Field(default=None)
    auto_apply: bool = Field(default=False)
    worktree_id: str | None = Field(default=None)
    worktree_kept: bool = Field(default=False)

    def __init__(self, **data: Any) -> None:
        """Initialize SessionRecord, coercing string enums to Enum instances."""
        if "status" in data and isinstance(data["status"], str):
            data["status"] = SessionStatus(data["status"])
        super().__init__(**data)

    @property
    def duration_seconds(self) -> float | None:
        """Elapsed execution duration in seconds, or None if unfinished or unparseable."""
        if not self.started_at or not self.completed_at:
            return None
        start = parse_timestamp(self.started_at)
        end = parse_timestamp(self.completed_at)
        if start is None or end is None:
            return None
        elapsed = (end - start).total_seconds()
        return elapsed if elapsed >= 0 else None


class ArtifactRecord(SQLModel, table=True):
    """Row shape for the centralized `artifacts` table."""

    __tablename__: ClassVar[str] = "artifacts"  # pyright: ignore[reportIncompatibleVariableOverride]
    model_config = {"extra": "forbid"}
    __table_args__ = (UniqueConstraint("project_id", "session_id", "name", name="uq_artifacts_project_session_name"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    session_id: str = Field(index=True, nullable=False)
    name: str
    path: Path = Field(sa_type=PathType)
    size_bytes: int = Field(default=0)
    file_count: int = Field(default=0)
    created_at: str = Field(default_factory=_now_utc_str)
    expires_at: str | None = Field(default=None, index=True)

    def __init__(self, **data: Any) -> None:
        """Initialize ArtifactRecord, coercing a string path to a Path instance."""
        if "path" in data and isinstance(data["path"], str):
            data["path"] = Path(data["path"])
        super().__init__(**data)


class CostRecord(SQLModel, table=True):
    """Row shape for the centralized `costs` table."""

    __tablename__: ClassVar[str] = "costs"  # pyright: ignore[reportIncompatibleVariableOverride]
    model_config = {"extra": "forbid"}

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    session_id: str = Field(index=True)
    branch_name: str
    model_id: str
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    estimated_usd_cost: float = Field(default=0.0)
    created_at: str = Field(default_factory=_now_utc_str, index=True)
