"""Diagnostic check validating worktree directories against DB and Git state."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.common.diagnostic_log import DiagnosticRecord, error_diagnostics
from dovo.common.exceptions import Failure, catch_failure
from dovo.core.db import WorktreesRepository
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DiagnosticsContext
from dovo.core.worktree.models import StaleWorktreeItem, WorktreeDetection
from dovo.core.worktree.services.detector import detect_stale_worktrees


class WorktreeRefsCheck:
    """Diagnostic check validating worktree directories against WorktreesRepository and Git worktree state."""

    check_id: str = "worktree.refs"
    name: str = "Worktree References Check"
    category: CheckCategory = CheckCategory.WORKTREE

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Scan worktree directories against DB records and Git worktree metadata for stale or orphaned entries."""
        db_path = context.paths.database_file
        if not db_path.is_file():
            return _ok_result(self.check_id, self.name, self.category, verified_count=0, diagnostics=[])

        db = WorktreesRepository(db_path=db_path, project_id=context.paths.project_id, auto_init=False)
        with catch_failure("diagnostics.worktree_refs", path=str(context.paths.root_dir)) as caught:
            detection = detect_stale_worktrees(context.paths, db, git=context.git)
        if caught.failure is not None:
            return _detection_unavailable_result(self.check_id, self.name, self.category, caught.failure)
        return _detection_result(self.check_id, self.name, self.category, detection)


def _detection_result(
    check_id: str, name: str, category: CheckCategory, detection: WorktreeDetection
) -> DiagnosticCheckResult:
    """Return the stale, orphan or OK result for a completed detection, stale taking priority."""
    diagnostics = list(detection.diagnostics)
    stale_items = [*detection.stale_worktrees, *detection.stale_db_records]
    if stale_items:
        return _stale_result(check_id, name, category, _identifiers(stale_items), diagnostics=diagnostics)

    if detection.orphaned_directories:
        orphan_dirs = _identifiers(detection.orphaned_directories)
        return _orphan_result(check_id, name, category, orphan_dirs, diagnostics=diagnostics)

    return _ok_result(check_id, name, category, verified_count=detection.active_worktree_count, diagnostics=diagnostics)


def _identifiers(items: Sequence[StaleWorktreeItem]) -> list[str]:
    """Return the identifier field of each stale worktree item, preserving order."""
    return [item.identifier for item in items]


def _detection_unavailable_result(
    check_id: str, name: str, category: CheckCategory, failure: Failure
) -> DiagnosticCheckResult:
    """Build the WARNING result naming the detection failure's error_code and message, carrying its recorded diagnostic."""
    message = f"Worktree detection could not complete ({failure.error_code.value}): {failure.message}"
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"error_code": failure.error_code.value},
        error_code=None,
        errors=[],
        warnings=[message],
        diagnostics=error_diagnostics(failure.diagnostic),
    )


def _stale_result(
    check_id: str, name: str, category: CheckCategory, stale_ids: list[str], *, diagnostics: list[DiagnosticRecord]
) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_WORKTREE_STALE result naming the stale worktree identifiers."""
    message = f"{len(stale_ids)} stale worktree reference(s) detected."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"stale_ids": stale_ids},
        error_code="DOCTOR_WORKTREE_STALE",
        errors=[],
        warnings=[message],
        diagnostics=diagnostics,
    )


def _orphan_result(
    check_id: str, name: str, category: CheckCategory, orphan_dirs: list[str], *, diagnostics: list[DiagnosticRecord]
) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_WORKTREE_ORPHAN result naming the orphaned directory names."""
    message = f"{len(orphan_dirs)} orphaned worktree directory(s) detected."
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.WARNING,
        message=message,
        details={"orphan_directories": orphan_dirs},
        error_code="DOCTOR_WORKTREE_ORPHAN",
        errors=[],
        warnings=[message],
        diagnostics=diagnostics,
    )


def _ok_result(
    check_id: str, name: str, category: CheckCategory, verified_count: int, *, diagnostics: list[DiagnosticRecord]
) -> DiagnosticCheckResult:
    """Build the OK result carrying the count of worktrees verified against DB and Git state."""
    return DiagnosticCheckResult(
        check_id=check_id,
        name=name,
        category=category,
        status=CheckStatus.OK,
        message=f"{verified_count} worktree(es) verified against database and Git worktree state.",
        details={"verified_count": verified_count},
        error_code=None,
        errors=[],
        warnings=[],
        diagnostics=diagnostics,
    )
