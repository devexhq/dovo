"""Diagnostic check validating write access across configured workspace paths."""

from __future__ import annotations

import uuid
from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.doctor.models import CheckCategory, CheckStatus, DiagnosticCheckResult, DoctorContext

NOT_INITIALIZED_WARNING = "Skipped sessions_dir/artifacts_dir probes because the workspace is not initialized."


class FilesystemWritableCheck:
    """Diagnostic check validating write access across configured workspace paths."""

    check_id: str = "filesystem.writable"
    name: str = "Filesystem Writable Check"
    category: CheckCategory = CheckCategory.FILESYSTEM

    def execute(self, context: DoctorContext) -> DiagnosticCheckResult:
        """Probe-write every resolved workspace directory (plus sandboxes) under context.paths."""
        targets = _target_paths(context.paths)
        not_initialized_warnings = [NOT_INITIALIZED_WARNING] if context.paths.project_id is None else []

        verified_paths: list[str] = []
        unwritable_paths: list[str] = []
        for path in targets.values():
            if _is_path_writable(path):
                verified_paths.append(str(path))
            else:
                unwritable_paths.append(str(path))

        if unwritable_paths:
            message = f"{len(unwritable_paths)} configured path(s) are not writable."
            return DiagnosticCheckResult(
                check_id=self.check_id,
                name=self.name,
                category=self.category,
                status=CheckStatus.FAILED,
                message=message,
                details={"unwritable_paths": unwritable_paths},
                duration_ms=0.0,
                error_code="DOCTOR_FS_UNWRITABLE",
                errors=[message],
                warnings=not_initialized_warnings,
                fixes=[],
            )

        return DiagnosticCheckResult(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=CheckStatus.OK,
            message="All configured workspace paths are writable.",
            details={"verified_paths": verified_paths},
            duration_ms=0.0,
            error_code=None,
            errors=[],
            warnings=not_initialized_warnings,
            fixes=[],
        )


def _target_paths(paths: WorkspacePaths) -> dict[str, Path]:
    """Return the ordered label-to-directory mapping of paths to probe for write access.

    sessions_dir/artifacts_dir are omitted when no project identity exists, so this check
    never mkdirs a repo-local runtime-storage placeholder for an uninitialized workspace.
    """
    targets: dict[str, Path] = {"root_dir": paths.dovo_dir}
    if paths.project_id is not None:
        targets["sessions_dir"] = paths.sessions_dir
        targets["artifacts_dir"] = paths.artifacts_dir
    targets["sandboxes_dir"] = paths.sandboxes_dir
    targets["database"] = paths.database_file.parent
    return targets


def _is_path_writable(path: Path) -> bool:
    """Create path if missing and verify a unique probe file can be written and removed."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    probe_path = path / f".probe-{uuid.uuid4()}.tmp"
    try:
        probe_path.write_text("", encoding="utf-8")
    except OSError:
        return False
    finally:
        probe_path.unlink(missing_ok=True)

    return True
