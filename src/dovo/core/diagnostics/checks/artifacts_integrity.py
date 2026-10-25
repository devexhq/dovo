"""Diagnostic check comparing artifact revision directories on disk with database rows."""

from __future__ import annotations

from pathlib import Path

from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DiagnosticsContext


class ArtifactsIntegrityCheck:
    """Diagnostic check comparing artifact revision directories on disk with ArtifactsRepository rows."""

    check_id: str = "artifacts.integrity"
    name: str = "Artifacts Integrity Check"
    category: CheckCategory = CheckCategory.ARTIFACTS

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
        """Report revision directories without a row and rows without a directory as WARNINGs; repair nothing."""
        db_path = context.paths.database_file
        if not db_path.is_file():
            return _ok_result(self, verified_count=0)

        db = ArtifactsRepository(db_path=db_path, project_id=context.paths.project_id, auto_init=False)
        rows = {record.path.resolve(): f"{record.session_id}/{record.name}@{record.revision}" for record in db.list()}
        directories = _revision_directories(context.paths.artifacts_dir)

        missing = sorted(label for path, label in rows.items() if path not in directories)
        if missing:
            return _missing_result(self, missing)

        orphans = sorted(str(path) for path in directories - rows.keys())
        if orphans:
            return _orphan_result(self, orphans)

        return _ok_result(self, verified_count=len(rows))


def _revision_directories(artifacts_dir: Path) -> set[Path]:
    """Return <session>/<name>/<revision> directories (and legacy flat <session>/<name> ones) holding manifest.json, never bundle contents or .tmp-* staging."""
    if not artifacts_dir.is_dir():
        return set()
    revisions = (m for m in artifacts_dir.glob("*/*/*/manifest.json") if m.parent.name.isdigit())
    legacy = artifacts_dir.glob("*/*/manifest.json")
    return {
        manifest.parent.resolve()
        for manifest in (*revisions, *legacy)
        if not any(part.startswith(".tmp-") for part in manifest.relative_to(artifacts_dir).parts)
    }


def _orphan_result(check: ArtifactsIntegrityCheck, orphan_dirs: list[str]) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_ARTIFACT_ORPHAN_DIR result naming directories without a row."""
    message = f"{len(orphan_dirs)} artifact revision directory(s) without a database row detected."
    return DiagnosticCheckResult(
        check_id=check.check_id,
        name=check.name,
        category=check.category,
        status=CheckStatus.WARNING,
        message=message,
        details={"orphan_directories": orphan_dirs},
        error_code="DOCTOR_ARTIFACT_ORPHAN_DIR",
        errors=[],
        warnings=[message],
    )


def _missing_result(check: ArtifactsIntegrityCheck, missing: list[str]) -> DiagnosticCheckResult:
    """Build the WARNING DOCTOR_ARTIFACT_MISSING_DIR result naming rows as '<session>/<name>@<revision>'."""
    message = f"{len(missing)} artifact revision row(s) without a directory detected."
    return DiagnosticCheckResult(
        check_id=check.check_id,
        name=check.name,
        category=check.category,
        status=CheckStatus.WARNING,
        message=message,
        details={"missing_directories": missing},
        error_code="DOCTOR_ARTIFACT_MISSING_DIR",
        errors=[],
        warnings=[message],
    )


def _ok_result(check: ArtifactsIntegrityCheck, verified_count: int) -> DiagnosticCheckResult:
    """Build the OK result carrying the count of artifact revisions verified against disk."""
    return DiagnosticCheckResult(
        check_id=check.check_id,
        name=check.name,
        category=check.category,
        status=CheckStatus.OK,
        message=f"{verified_count} artifact revision(s) verified against the database.",
        details={"verified_count": verified_count},
        error_code=None,
        errors=[],
        warnings=[],
    )
