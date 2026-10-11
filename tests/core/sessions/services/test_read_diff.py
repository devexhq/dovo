"""Tests for reading a session's persisted diff artifact."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.sessions import DiffStatus
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
    @pytest.mark.parametrize(
        ("patch_text", "expected_status"),
        [
            pytest.param(None, DiffStatus.DIFF_NOT_FOUND, id="missing_patch"),
            pytest.param("  \n\n", DiffStatus.EMPTY_DIFF, id="whitespace_only_patch"),
        ],
    )
    def test_read_session_diff_with_absent_or_blank_patch_returns_classified_status(
        self,
        tmp_path: Path,
        workspace_paths_factory: WorkspacePathsFactory,
        patch_text: str | None,
        expected_status: DiffStatus,
    ) -> None:
        """[tier-1/integration] read_session_diff: no diff.patch -> DIFF_NOT_FOUND; blank diff.patch -> EMPTY_DIFF with diff_text ''; both keep session_id and artifact_path, and ok is True only for EMPTY_DIFF."""
        paths = workspace_paths_factory(tmp_path, None)
        if patch_text is None:
            paths.session_dir("session-3").mkdir(parents=True)
        else:
            _write_patch(paths, "session-3", patch_text)

        result = read_session_diff(paths, "session-3")

        assert result.status == expected_status
        assert result.session_id == "session-3"
        assert result.artifact_path == paths.session_dir("session-3") / "diff.patch"
        assert result.diff_text == ""
        assert result.ok is (expected_status == DiffStatus.EMPTY_DIFF)

    def test_read_session_diff_with_missing_patch_fix_names_actual_artifact_path(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] read_session_diff: no diff.patch -> fixes names the resolved artifact path, not a repo-local .dovo/sessions path."""
        paths = workspace_paths_factory(tmp_path, None)
        paths.session_dir("session-5").mkdir(parents=True)

        result = read_session_diff(paths, "session-5")

        assert result.fixes == [
            f"Verify the session generated a diff artifact at {paths.session_dir('session-5') / 'diff.patch'}"
        ]

    def test_read_session_diff_with_written_patch_returns_ok_with_exact_text(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] read_session_diff: a written diff.patch -> status OK, diff_text equal to the file text, raw False, full False, max_lines None."""
        paths = workspace_paths_factory(tmp_path, None)
        patch_text = "diff --git a/f.txt b/f.txt\n-old\n+new\n"
        _write_patch(paths, "session-1", patch_text)

        result = read_session_diff(paths, "session-1")

        assert result.status == DiffStatus.OK
        assert result.diff_text == patch_text
        assert (result.raw, result.full, result.max_lines) == (False, False, None)

    def test_read_session_diff_with_unreadable_patch_returns_read_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] read_session_diff: an OSError reading diff.patch -> READ_FAILURE with errors == [f"Failed to read diff artifact at '{patch_path}': denied"] and ok False."""
        paths = workspace_paths_factory(tmp_path, None)
        patch_path = _write_patch(paths, "session-4", "diff --git a/f.txt b/f.txt\n")

        def _deny_read(self: Path, *args: object, **kwargs: object) -> str:
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "read_text", _deny_read)

        result = read_session_diff(paths, "session-4")

        assert result.status == DiffStatus.READ_FAILURE
        assert result.artifact_path == patch_path
        assert result.errors == [f"Failed to read diff artifact at '{patch_path}': denied"]
        assert result.ok is False
