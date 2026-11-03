"""Tests for reading a session's persisted diff artifact."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.sessions import SessionError
from dovo.core.sessions.services.read_diff import read_session_diff

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _write_patch(paths: WorkspacePaths, session_id: str, diff_text: str) -> Path:
    """Persist diff_text as the session's diff.patch artifact and return its path."""
    session_dir = paths.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    patch_path = session_dir / "diff.patch"
    patch_path.write_text(diff_text, encoding="utf-8")
    return patch_path


class ReadSessionDiffTests:
    def test_read_session_diff_with_written_patch_returns_path_and_exact_text(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        patch_text = "diff --git a/f.txt b/f.txt\n-old\n+new\n"
        patch_path = _write_patch(paths, "session-1", patch_text)

        assert read_session_diff(paths, "session-1") == (patch_path, patch_text)

    @pytest.mark.parametrize("patch_text", [pytest.param("", id="empty"), pytest.param("  \n\n", id="whitespace")])
    def test_read_session_diff_with_blank_patch_returns_empty_text(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory, patch_text: str
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        patch_path = _write_patch(paths, "session-3", patch_text)

        assert read_session_diff(paths, "session-3") == (patch_path, "")

    def test_read_session_diff_with_missing_patch_raises_diff_not_found_naming_the_artifact_path(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        paths.session_dir("session-5").mkdir(parents=True)
        patch_path = paths.session_dir("session-5") / "diff.patch"

        with pytest.raises(SessionError) as raised:
            read_session_diff(paths, "session-5")

        assert raised.value.error_code is ErrorCode.SESSION_DIFF_NOT_FOUND
        assert raised.value.message == "Session 'session-5' has no diff artifact."
        assert raised.value.remediations == (
            Remediation(message=f"Verify the session generated a diff artifact at '{patch_path}'"),
        )
        assert raised.value.__cause__ is None

    def test_read_session_diff_with_unreadable_patch_raises_diff_unreadable_chained_to_the_os_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        paths = workspace_paths_factory(tmp_path, None)
        patch_path = _write_patch(paths, "session-4", "diff --git a/f.txt b/f.txt\n")
        denied = PermissionError("denied")

        def _deny_read(self: Path, *args: object, **kwargs: object) -> str:
            raise denied

        monkeypatch.setattr(Path, "read_text", _deny_read)

        with pytest.raises(SessionError) as raised:
            read_session_diff(paths, "session-4")

        assert raised.value.error_code is ErrorCode.SESSION_DIFF_UNREADABLE
        assert raised.value.message == f"Failed to read diff artifact at '{patch_path}': denied"
        assert raised.value.remediations == (
            Remediation(message="Check file permissions and that the artifact is readable"),
        )
        assert raised.value.__cause__ is denied
