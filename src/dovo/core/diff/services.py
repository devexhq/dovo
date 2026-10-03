"""Class-based execution service for diff inspection operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.diff.models import DiffResult, DiffStatus


@dataclass
class DiffService:
    """Service encapsulating session unified diff retrieval."""

    paths: WorkspacePaths
    session_id: str | None = None
    raw: bool = False
    full: bool = False
    max_lines: int | None = None

    def _discover_latest_session(self, sessions_dir: Path) -> Path | None:
        """Discover the most recently modified session directory under sessions_dir."""
        if not sessions_dir.is_dir():
            return None

        candidate_dirs = [entry for entry in sessions_dir.iterdir() if entry.is_dir()]
        if not candidate_dirs:
            return None

        return max(candidate_dirs, key=lambda entry: (entry.stat().st_mtime, entry.name))

    def _resolve_session_target(
        self,
        sessions_dir: Path,
    ) -> tuple[Path | None, str | None, DiffResult | None]:
        """Resolve target session directory and ID or return error DiffResult."""
        if self.session_id is not None:
            target_dir = sessions_dir / self.session_id
            if not target_dir.is_dir():
                return (
                    None,
                    None,
                    DiffResult(
                        status=DiffStatus.SESSION_NOT_FOUND,
                        session_id=self.session_id,
                        errors=[f"Session '{self.session_id}' not found under .dovo/sessions/."],
                        fixes=["Run `dovo sandbox list` or check .dovo/sessions/ for valid session IDs"],
                    ),
                )
            return target_dir, self.session_id, None

        latest_dir = self._discover_latest_session(sessions_dir)
        if latest_dir is None:
            return (
                None,
                None,
                DiffResult(
                    status=DiffStatus.SESSION_NOT_FOUND,
                    errors=["No loop run sessions found."],
                    fixes=["Run `dovo sandbox list` or check .dovo/sessions/ for valid session IDs"],
                ),
            )

        return latest_dir, latest_dir.name, None

    def _read_patch_artifact(self, target_dir: Path, session_id: str) -> DiffResult:
        """Read and validate diff.patch artifact within the target session directory."""
        patch_file = target_dir / "diff.patch"
        if not patch_file.is_file():
            return DiffResult(
                status=DiffStatus.DIFF_NOT_FOUND,
                session_id=session_id,
                artifact_path=patch_file,
                errors=[f"Session '{session_id}' has no diff artifact."],
                fixes=[f"Verify the session generated a diff artifact at .dovo/sessions/{session_id}/diff.patch"],
            )

        try:
            diff_text = patch_file.read_text(encoding="utf-8")
        except OSError as exc:
            return DiffResult(
                status=DiffStatus.READ_FAILURE,
                session_id=session_id,
                artifact_path=patch_file,
                errors=[f"Failed to read diff artifact at '{patch_file}': {exc}"],
                fixes=["Check file permissions and that the artifact is readable"],
            )

        if not diff_text.strip():
            return DiffResult(
                status=DiffStatus.EMPTY_DIFF,
                session_id=session_id,
                artifact_path=patch_file,
                diff_text="",
                raw=self.raw,
                full=self.full,
                max_lines=self.max_lines,
            )

        return DiffResult(
            status=DiffStatus.OK,
            session_id=session_id,
            artifact_path=patch_file,
            diff_text=diff_text,
            raw=self.raw,
            full=self.full,
            max_lines=self.max_lines,
        )

    def collect(self) -> DiffResult:
        """Collect and validate the diff artifact without side effects."""
        sessions_dir = self.paths.sessions_dir

        target_dir, resolved_session_id, error_result = self._resolve_session_target(sessions_dir)
        if error_result is not None or target_dir is None or resolved_session_id is None:
            return error_result or DiffResult(
                status=DiffStatus.SESSION_NOT_FOUND,
                errors=["Session resolution failed."],
            )

        return self._read_patch_artifact(target_dir, resolved_session_id)

    def execute(self) -> DiffResult:
        """Collect diff artifact and return structured result."""
        return self.collect()
