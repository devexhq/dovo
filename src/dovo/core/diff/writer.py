"""Session artifact directory and diff persistence helpers."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import Filesystem, WorkspacePaths


def get_session_dir(paths: WorkspacePaths, session_id: str) -> Path:
    """Resolve and create the project-aware session artifact directory on demand."""
    target = paths.session_dir(session_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_session_diff(session_dir: Path, diff_text: str) -> Path:
    """Atomically write unified diff to diff.patch in the session directory."""
    target_file = session_dir / "diff.patch"
    Filesystem.atomic_write_text(target_file, diff_text)
    return target_file
