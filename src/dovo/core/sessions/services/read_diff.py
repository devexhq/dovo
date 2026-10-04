"""Read a session's persisted diff.patch artifact."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.sessions.models import DiffResult, DiffStatus


def read_session_diff(paths: WorkspacePaths, session_id: str) -> DiffResult:
    """Read a session's diff.patch into OK, EMPTY_DIFF, DIFF_NOT_FOUND, or READ_FAILURE."""
    patch_file = paths.session_dir(session_id) / "diff.patch"
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
        )

    return DiffResult(
        status=DiffStatus.OK,
        session_id=session_id,
        artifact_path=patch_file,
        diff_text=diff_text,
    )
