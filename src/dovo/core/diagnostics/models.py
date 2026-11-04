"""Domain models and protocol for diagnostic health checks."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.config.models import DovoConfig
from dovo.core.git import GitRunner


class CheckStatus(StrEnum):
    """Classified outcome of a diagnostic check execution."""

    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckCategory(StrEnum):
    """Category classification for diagnostic checks."""

    GIT = "git"
    CONFIG = "config"
    FILESYSTEM = "filesystem"
    WORKTREE = "worktree"
    AGENT = "agent"
    ENVIRONMENT = "environment"
    ARTIFACTS = "artifacts"


class DiagnosticsContext(BaseModel):
    """Contextual environment supplied to diagnostic checks during execution."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    cwd: Path
    config: DovoConfig | None = None
    paths: WorkspacePaths
    git: GitRunner


class DiagnosticCheckRecord(BaseModel):
    """One diagnostic check's outcome: a record, not a command result, so a warning check carries its code with no errors.

    error_code is a published ErrorCode or None; any other value, or a FAILED record without one, is refused when the
    record is built.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    check_id: str
    name: str
    category: CheckCategory
    status: CheckStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    error_code: ErrorCode | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)  # error-level records only

    @model_validator(mode="after")
    def _require_error_code_when_failed(self) -> Self:
        """Refuse a FAILED record that carries no error_code."""
        if self.status == CheckStatus.FAILED and self.error_code is None:
            raise ValueError("a failed check must carry an error_code")
        return self

    @property
    def ok(self) -> bool:
        """Return True when check passed or was skipped."""
        return self.status in (CheckStatus.OK, CheckStatus.SKIPPED)


class DiagnosticsReport(BaseModel):
    """Aggregated report of diagnostic check executions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_root: Path
    checks: list[DiagnosticCheckRecord]
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)  # every check's records, in check order
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Return True when no checks FAILED (OK, WARNING, SKIPPED)."""
        return all(c.status != CheckStatus.FAILED for c in self.checks)

    @property
    def error_code(self) -> ErrorCode | None:
        """Return the error_code of the first FAILED check, or None when no check failed; not part of the payload."""
        return next((c.error_code for c in self.checks if c.status == CheckStatus.FAILED), None)

    @property
    def has_warnings(self) -> bool:
        """Return True when at least one executed check has status WARNING."""
        return any(c.status == CheckStatus.WARNING for c in self.checks)


@runtime_checkable
class DiagnosticCheck(Protocol):
    """Provider-agnostic contract for diagnostic health checks."""

    check_id: str
    name: str
    category: CheckCategory

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckRecord:
        """Execute check against context and return its DiagnosticCheckRecord."""
        ...
