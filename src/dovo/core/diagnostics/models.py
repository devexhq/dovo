"""Domain models and protocol for diagnostic health checks."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import BaseResult
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


class DiagnosticCheckResult(BaseResult):
    """Individual diagnostic check outcome."""

    model_config = ConfigDict(extra="forbid", strict=True)

    check_id: str
    name: str
    category: CheckCategory
    status: CheckStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Return True when check passed or was skipped."""
        return self.status in (CheckStatus.OK, CheckStatus.SKIPPED)


class DiagnosticsReport(BaseModel):
    """Aggregated report of diagnostic check executions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_root: Path
    checks: list[DiagnosticCheckResult]
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Return True when no checks FAILED (OK, WARNING, SKIPPED)."""
        return all(c.status != CheckStatus.FAILED for c in self.checks)

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

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Execute check against context and return typed DiagnosticCheckResult."""
        ...
