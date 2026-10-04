"""Domain models and protocol for diagnostic health checks."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import BaseResult
from dovo.core.config.models import DovoConfig


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


class DiagnosticsContext(BaseModel):
    """Contextual environment supplied to diagnostic checks during execution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    cwd: Path
    config: DovoConfig | None = None
    paths: WorkspacePaths


class RemediationType(StrEnum):
    """Classification of how a Remediation should be carried out."""

    COMMAND = "command"
    MANUAL = "manual"


class Remediation(BaseModel):
    """Deterministic, copy-pasteable remediation action for a failing or warning diagnostic check."""

    model_config = {"extra": "forbid", "strict": True}

    code: str
    title: str
    action_type: RemediationType
    command: str | None = None
    description: str
    doc_path: str | None = None
    is_automated: bool = False


class DiagnosticCheckResult(BaseResult):
    """Individual diagnostic check outcome."""

    model_config = ConfigDict(extra="forbid", strict=True)

    check_id: str
    name: str
    category: CheckCategory
    status: CheckStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    remediations: list[Remediation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when check passed or was skipped."""
        return self.status in (CheckStatus.OK, CheckStatus.SKIPPED)


class DiagnosticsReport(BaseModel):
    """Aggregated report of diagnostic check executions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_root: Path
    checks: list[DiagnosticCheckResult]
    total_duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """Return True when no checks FAILED (OK, WARNING, SKIPPED)."""
        return all(c.status != CheckStatus.FAILED for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        """Return True when at least one executed check has status WARNING."""
        return any(c.status == CheckStatus.WARNING for c in self.checks)

    @property
    def failed_checks(self) -> list[DiagnosticCheckResult]:
        """Return list of executed checks with status FAILED."""
        return [c for c in self.checks if c.status == CheckStatus.FAILED]

    @property
    def warning_checks(self) -> list[DiagnosticCheckResult]:
        """Return list of executed checks with status WARNING."""
        return [c for c in self.checks if c.status == CheckStatus.WARNING]


@runtime_checkable
class DiagnosticCheck(Protocol):
    """Provider-agnostic contract for diagnostic health checks."""

    check_id: str
    name: str
    category: CheckCategory

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Execute check against context and return typed DiagnosticCheckResult."""
        ...
