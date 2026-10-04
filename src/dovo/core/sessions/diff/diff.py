"""Diff domain entrypoint and session diff artifact retrieval."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.sessions.diff.models import DiffResult, DiffStatus


class Diff:
    """Entrypoint for inspecting a session's persisted diff artifact."""

    def __init__(self, paths: WorkspacePaths, raw: bool = False, full: bool = False, max_lines: int = 500) -> None:
        self.paths = paths
        self.raw = raw
        self.full = full
        self.max_lines = max_lines

    def inspect(self, session_id: str | None = None) -> DiffResult:
        """Inspect and return structured diff result for a session or latest run."""
        target_dir = self._resolve_session_dir(session_id)
        if isinstance(target_dir, DiffResult):
            return target_dir

        return self._read_patch_artifact(target_dir, session_id if session_id is not None else target_dir.name)

    @staticmethod
    def _discover_latest_session(sessions_dir: Path) -> Path | None:
        """Discover the most recently modified session directory under sessions_dir."""
        if not sessions_dir.is_dir():
            return None

        candidate_dirs = [entry for entry in sessions_dir.iterdir() if entry.is_dir()]
        if not candidate_dirs:
            return None

        return max(candidate_dirs, key=lambda entry: (entry.stat().st_mtime, entry.name))

    def _resolve_session_dir(self, session_id: str | None) -> Path | DiffResult:
        """Return the target session directory, or a SESSION_NOT_FOUND result."""
        sessions_dir = self.paths.sessions_dir
        if session_id is not None:
            target_dir = sessions_dir / session_id
            if target_dir.is_dir():
                return target_dir

            return DiffResult(
                status=DiffStatus.SESSION_NOT_FOUND,
                session_id=session_id,
                errors=[f"Session '{session_id}' not found under .dovo/sessions/."],
                fixes=["Run `dovo worktree list` or check .dovo/sessions/ for valid session IDs"],
            )

        latest_dir = self._discover_latest_session(sessions_dir)
        if latest_dir is not None:
            return latest_dir

        return DiffResult(
            status=DiffStatus.SESSION_NOT_FOUND,
            errors=["No sessions found under .dovo/sessions/."],
            fixes=["Run `dovo worktree list` or check .dovo/sessions/ for valid session IDs"],
        )

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
