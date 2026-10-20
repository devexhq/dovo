"""Pydantic and SQLModel record models and enums for the database layer."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from sqlalchemy import Dialect, String, TypeDecorator, UniqueConstraint
from sqlmodel import Field, SQLModel
from sqlmodel.main import SQLModelConfig


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

    def process_bind_param(self, value: Path | str | None, dialect: Dialect) -> str | None:
        """Coerce incoming Path or str object to string for SQLite storage."""
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Path | None:
        """Coerce retrieved database string value back into a Path instance."""
        if value is None:
            return None
        return Path(value)


class StrEnumType[E: StrEnum](TypeDecorator[E]):
    """Generic SQLAlchemy type storing a StrEnum member as its string value."""

    impl = String
    cache_ok = True

    def __init__(self, enum_cls: type[E]) -> None:
        """Bind the column to enum_cls; kept as a public attribute so it enters the type's cache key."""
        super().__init__()
        self.enum_cls = enum_cls

    def process_bind_param(self, value: E | str | None, dialect: Dialect) -> str | None:
        """Bind an enum_cls member or a str to its string value, and None to None."""
        if value is None:
            return None
        return value.value if isinstance(value, self.enum_cls) else str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> E | None:
        """Load a stored string as its enum_cls member, None as None; a non-member raises ValueError."""
        if value is None:
            return None
        return self.enum_cls(value)


def _coercion_target(annotation: object) -> type[Path] | type[StrEnum] | None:
    """Return Path or the StrEnum subclass a field annotation names exactly, else None."""
    if annotation is Path:
        return Path
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return annotation
    return None


def _coerce(target: type[Path] | type[StrEnum], value: object) -> object:
    """Return target(value) when value is a str not already of target; otherwise value unchanged."""
    if isinstance(value, str) and not isinstance(value, target):
        return target(value)
    return value


class RecordModel(SQLModel):
    """Base for table records that coerces str values of Path and StrEnum fields on construction."""

    def model_post_init(self, context: object, /) -> None:
        """Replace each str value of a Path or StrEnum field with the declared type, assigning only changed values; unknown enum values raise ValueError."""
        for name, field in type(self).model_fields.items():
            target = _coercion_target(field.annotation)
            if target is None:
                continue
            value = getattr(self, name)
            coerced = _coerce(target, value)
            if coerced is not value:
                setattr(self, name, coerced)


class WorktreeRecord(RecordModel, table=True):
    """Row shape for the centralized `worktrees` table."""

    __tablename__: ClassVar[str] = "worktrees"
    model_config = SQLModelConfig(extra="forbid")

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    name: str | None = Field(default=None)
    branch_name: str
    base_commit: str
    worktree_path: Path = Field(sa_type=PathType, unique=True)
    status: WorktreeStatus = Field(
        default=WorktreeStatus.ACTIVE,
        sa_type=StrEnumType(WorktreeStatus),  # ty: ignore[invalid-argument-type]  # SQLModel stubs type sa_type as type[Any]; Column accepts a TypeEngine instance
        index=True,
    )
    created_at: str = Field(default_factory=_now_utc_str)
    updated_at: str = Field(default_factory=_now_utc_str)


class SessionRecord(RecordModel, table=True):
    """Row shape for the centralized `sessions` table."""

    __tablename__: ClassVar[str] = "sessions"
    model_config = SQLModelConfig(extra="forbid")

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    session_id: str = Field(unique=True)
    blueprint_key: str
    blueprint_name: str
    branch_name: str = Field(default="")
    status: SessionStatus = Field(
        default=SessionStatus.RUNNING,
        sa_type=StrEnumType(SessionStatus),  # ty: ignore[invalid-argument-type]  # SQLModel stubs type sa_type as type[Any]; Column accepts a TypeEngine instance
        index=True,
    )
    pid: int | None = Field(default=None)
    pid_started_at: str | None = Field(default=None)
    started_at: str = Field(default_factory=_now_utc_str, index=True)
    completed_at: str | None = Field(default=None)
    error_message: str | None = Field(default=None)

    execution_state_json: str | None = Field(default=None)
    execution_state_revision: int | None = Field(default=0)
    blueprint_tier: str | None = Field(default=None)
    commit_sha: str | None = Field(default=None)
    use_worktree: bool = Field(default=True)
    keep: bool = Field(default=False)
    discard: bool = Field(default=False)
    agent: str | None = Field(default=None)
    inputs_json: str | None = Field(default=None)
    auto_apply: bool = Field(default=False)
    worktree_id: str | None = Field(default=None)
    worktree_kept: bool = Field(default=False)

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


class ArtifactRecord(RecordModel, table=True):
    """Row shape for the centralized `artifacts` table."""

    __tablename__: ClassVar[str] = "artifacts"
    model_config = SQLModelConfig(extra="forbid")
    __table_args__ = (
        UniqueConstraint(
            "project_id", "session_id", "name", "revision", name="uq_artifacts_project_session_name_revision"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, nullable=False)
    session_id: str = Field(index=True, nullable=False)
    name: str
    path: Path = Field(sa_type=PathType)
    revision: int = Field(default=1)
    digest: str | None = Field(default=None)
    step_id: str | None = Field(default=None)
    attempt: int | None = Field(default=None)
    loop_iteration: int | None = Field(default=None)
    size_bytes: int = Field(default=0)
    file_count: int = Field(default=0)
    created_at: str = Field(default_factory=_now_utc_str)
    expires_at: str | None = Field(default=None, index=True)


class CostRecord(SQLModel, table=True):
    """Row shape for the centralized `costs` table."""

    __tablename__: ClassVar[str] = "costs"
    model_config = SQLModelConfig(extra="forbid")

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
