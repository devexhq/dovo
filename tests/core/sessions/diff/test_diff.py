"""Tests for the Diff entrypoint and session diff artifact retrieval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.sessions.diff.diff import Diff
from dovo.core.sessions.diff.models import DiffStatus

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


def _write_patch(paths: WorkspacePaths, session_id: str, diff_text: str) -> Path:
    """Persist diff_text as the session's diff.patch artifact and return its path."""
    session_dir = paths.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    patch_path = session_dir / "diff.patch"
    patch_path.write_text(diff_text, encoding="utf-8")
    return patch_path


class DiffInspectSessionResolutionTests:
    """Integration tests for reading routed session diff artifacts."""

    def test_inspect_with_project_identity_reads_global_session_patch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """A global patch is returned with its exact path and contents."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        patch_text = "diff --git a/file.txt b/file.txt\n-old\n+new\n"
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        save_project_identity(repository / ".dovo" / "project.json", identity)
        paths = workspace_paths_factory(repository, None)
        patch_path = _write_patch(paths, "session-626", patch_text)

        result = Diff(paths).inspect(session_id="session-626")

        assert result.status == DiffStatus.OK
        assert result.session_id == "session-626"
        assert result.diff_text == patch_text
        assert result.artifact_path == patch_path

    def test_inspect_with_project_identity_and_no_global_session_returns_session_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """An absent global session returns not found without creating a directory."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        save_project_identity(repository / ".dovo" / "project.json", identity)
        paths = workspace_paths_factory(repository, None)

        result = Diff(paths).inspect(session_id="session-626")

        session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "session-626"
        assert result.status == DiffStatus.SESSION_NOT_FOUND
        assert not session_dir.exists()

    def test_inspect_without_session_id_discovers_most_recently_modified_session(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] Diff.inspect: session_id omitted -> resolves the most recently modified session directory under sessions_dir, not the first alphabetically."""
        paths = workspace_paths_factory(tmp_path, None)
        _write_patch(paths, "aaa-older", "diff --git a/old.txt b/old.txt\n")
        _write_patch(paths, "zzz-newer", "diff --git a/new.txt b/new.txt\n")

        result = Diff(paths).inspect()

        assert result.status == DiffStatus.OK
        assert result.session_id == "zzz-newer"

    def test_inspect_without_session_id_and_no_sessions_returns_session_not_found(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] Diff.inspect: session_id omitted and no session directories exist -> SESSION_NOT_FOUND with no session_id set."""
        result = Diff(workspace_paths_factory(tmp_path, None)).inspect()

        assert result.status == DiffStatus.SESSION_NOT_FOUND
        assert result.session_id is None


class DiffInspectArtifactTests:
    """Integration tests for classified outcomes when a session's diff.patch is absent, empty, or unreadable."""

    @pytest.mark.parametrize(
        ("patch_text", "expected_status"),
        [
            pytest.param(None, DiffStatus.DIFF_NOT_FOUND, id="missing_patch"),
            pytest.param("  \n\n", DiffStatus.EMPTY_DIFF, id="whitespace_only_patch"),
        ],
    )
    def test_inspect_with_absent_or_empty_patch_returns_classified_status(
        self,
        tmp_path: Path,
        workspace_paths_factory: WorkspacePathsFactory,
        patch_text: str | None,
        expected_status: DiffStatus,
    ) -> None:
        """[tier-1/integration] Diff.inspect: a session without diff.patch -> DIFF_NOT_FOUND; a blank diff.patch -> EMPTY_DIFF with empty diff_text."""
        paths = workspace_paths_factory(tmp_path, None)
        if patch_text is None:
            paths.session_dir("session-3").mkdir(parents=True)
        else:
            _write_patch(paths, "session-3", patch_text)

        result = Diff(paths).inspect(session_id="session-3")

        assert result.status == expected_status
        assert result.session_id == "session-3"
        assert result.artifact_path == paths.session_dir("session-3") / "diff.patch"
        assert result.diff_text == ""
        assert result.ok is (expected_status == DiffStatus.EMPTY_DIFF)

    def test_inspect_with_unreadable_patch_returns_read_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] Diff.inspect: an OSError reading diff.patch -> READ_FAILURE naming the artifact and the cause."""
        paths = workspace_paths_factory(tmp_path, None)
        patch_path = _write_patch(paths, "session-4", "diff --git a/f.txt b/f.txt\n")

        def _deny_read(self: Path, *args: object, **kwargs: object) -> str:
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "read_text", _deny_read)

        result = Diff(paths).inspect(session_id="session-4")

        assert result.status == DiffStatus.READ_FAILURE
        assert result.artifact_path == patch_path
        assert result.errors == [f"Failed to read diff artifact at '{patch_path}': denied"]
        assert result.ok is False


class DiffInspectResultFlagsTests:
    """Integration tests for successful Diff.inspect results."""

    def test_inspect_returns_ok_for_a_session_with_a_persisted_patch(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] Diff.inspect: a session with a written diff.patch is returned with OK status and the exact diff text."""
        paths = workspace_paths_factory(tmp_path, None)
        patch_text = "diff --git a/f.txt b/f.txt\n-old\n+new\n"
        _write_patch(paths, "session-1", patch_text)

        result = Diff(paths).inspect(session_id="session-1")

        assert result.status == DiffStatus.OK
        assert result.diff_text == patch_text

    def test_inspect_passes_raw_full_and_max_lines_through_to_the_result(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] Diff.inspect: constructor's raw/full/max_lines flags are forwarded into the returned DiffResult."""
        paths = workspace_paths_factory(tmp_path, None)
        _write_patch(paths, "session-2", "diff --git a/f.txt b/f.txt\n")

        result = Diff(paths, raw=True, full=True, max_lines=10).inspect(session_id="session-2")

        assert result.raw is True
        assert result.full is True
        assert result.max_lines == 10
