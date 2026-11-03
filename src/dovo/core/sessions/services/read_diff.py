"""Read a session's persisted diff.patch artifact."""

from __future__ import annotations

from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.sessions.exceptions import SessionError


def read_session_diff(paths: WorkspacePaths, session_id: str) -> tuple[Path, str]:
    """Return the session's diff.patch path and text; the text is empty for an empty or whitespace-only patch.

    Raises:
        SessionError: SESSION_DIFF_NOT_FOUND when the session has no diff.patch, or SESSION_DIFF_UNREADABLE
            when it cannot be read.
    """
    patch_file = paths.session_dir(session_id) / "diff.patch"
    if not patch_file.is_file():
        raise SessionError(
            ErrorCode.SESSION_DIFF_NOT_FOUND,
            f"Session '{session_id}' has no diff artifact.",
            [Remediation(message=f"Verify the session generated a diff artifact at '{patch_file}'")],
        )

    try:
        diff_text = patch_file.read_text(encoding="utf-8")
    except OSError as error:
        # Chained: the session directory is Dovo's own state, so catch_failure records it.
        raise SessionError(
            ErrorCode.SESSION_DIFF_UNREADABLE,
            f"Failed to read diff artifact at '{patch_file}': {error}",
            [Remediation(message="Check file permissions and that the artifact is readable")],
        ) from error

    return patch_file, diff_text if diff_text.strip() else ""
