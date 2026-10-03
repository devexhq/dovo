"""Diff domain facade."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.diff.models import DiffResult
from dovo.core.diff.services import DiffService
from dovo.core.diff.writer import get_session_dir, write_session_diff


class Diff:
    """Unified entrypoint for execution run diff artifacts inspection and writing."""

    def __init__(self, paths: WorkspacePaths, raw: bool = False, full: bool = False, max_lines: int = 500) -> None:
        self.paths = paths
        self.raw = raw
        self.full = full
        self.max_lines = max_lines

    def inspect(self, session_id: str | None = None) -> DiffResult:
        """Inspect and return structured diff result for a session or latest run."""
        service = DiffService(
            paths=self.paths,
            session_id=session_id,
            raw=self.raw,
            full=self.full,
            max_lines=self.max_lines,
        )
        return service.collect()

    @staticmethod
    def session_dir(paths: WorkspacePaths, session_id: str) -> Path:
        """Return the target directory for storing session artifacts."""
        return get_session_dir(paths, session_id)

    @staticmethod
    def write(session_dir: Path, diff_text: str) -> Path:
        """Write session diff text to diff.patch atomically."""
        return write_session_diff(session_dir, diff_text)
