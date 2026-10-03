"""Diagnostic check validating sandbox worktree directories against DB and Git state."""

from __future__ import annotations

from dovo.core.db import SandboxesRepository
from dovo.core.doctor.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DoctorContext
from dovo.core.sandbox.models import SandboxDetectionResult, SandboxDetectionStatus, StaleSandboxItem
from dovo.core.sandbox.services.detector import detect_stale_sandboxes


class SandboxRefsCheck:
    """Diagnostic check validating sandbox directories against SandboxesRepository and Git worktree state."""

    check_id: str = "sandbox.refs"
    name: str = "Sandbox References Check"
    category: CheckCategory = CheckCategory.SANDBOX

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        """Scan sandbox directories against DB records and Git worktree metadata for stale or orphaned entries."""
        db_path = context.paths.database_file
        if not db_path.is_file():
            return _ok_result(self.check_id, self.name, self.category, verified_count=0)

        db = SandboxesRepository(db_path=db_path, project_id=context.paths.project_id, auto_init=False)
        detection = detect_stale_sandboxes(context.cwd, db)

        if detection.status != SandboxDetectionStatus.OK:
            return _detection_unavailable_result(self.check_id, self.name, self.category, detection)

        stale_items = [*detection.stale_worktrees, *detection.stale_db_records]
        if stale_items:
            return _stale_result(self.check_id, self.name, self.category, _identifiers(stale_items))

        if detection.orphaned_directories:
            return _orphan_result(self.check_id, self.name, self.category, _identifiers(detection.orphaned_directories))

        return _ok_result(self.check_id, self.name, self.category, verified_count=detection.active_sandbox_count)


def _identifiers(items: list[StaleSandboxItem]) -> list[str]:
    """Return the identifier field of each stale sandbox item, preserving order."""
    return [item.identifier for item in items]


def _detection_unavailable_result(
    check_id: str, name: str, category: CheckCategory, detection: SandboxDetectionResult
) -> DiagnosticCheckResult:
    """Build the WARNING result for a detection scan that could not complete (GIT_FAILED or ERROR)."""
    message = f"Sandbox detection could not complete (status='{detection.status.value}')."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"detection_status": detection.status.value},
        duration_ms=0.0,
        error_code=None,
        errors=[],
        warnings=[message],
        fixes=[],
    )


def _stale_result(check_id: str, name: str, category: CheckCategory, stale_ids: list[str]) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_SANDBOX_STALE result naming the stale sandbox identifiers."""
    message = f"{len(stale_ids)} stale sandbox reference(s) detected."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"stale_ids": stale_ids},
        duration_ms=0.0,
        error_code="DOCTOR_SANDBOX_STALE",
        errors=[],
        warnings=[message],
        fixes=[],
    )


def _orphan_result(check_id: str, name: str, category: CheckCategory, orphan_dirs: list[str]) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_SANDBOX_ORPHAN result naming the orphaned directory names."""
    message = f"{len(orphan_dirs)} orphaned sandbox directory(s) detected."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"orphan_directories": orphan_dirs},
        duration_ms=0.0,
        error_code="DOCTOR_SANDBOX_ORPHAN",
        errors=[],
        warnings=[message],
        fixes=[],
    )


def _ok_result(check_id: str, name: str, category: CheckCategory, verified_count: int) -> DiagnosticCheckResult:
    """Build the OK result carrying the count of sandboxes verified against DB and Git state."""
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message=f"{verified_count} sandbox(es) verified against database and Git worktree state.",
        details={"verified_count": verified_count},
        duration_ms=0.0,
        error_code=None,
        errors=[],
        warnings=[],
        fixes=[],
    )
