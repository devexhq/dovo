"""Tests for project-aware diff artifact retrieval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.diff.models import DiffStatus
from dovo.core.diff.services import DiffService
from dovo.core.diff.writer import get_session_dir, write_session_diff
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class DiffServiceTests:
    """Integration tests for reading routed session diff artifacts."""

    def test_collect_with_project_identity_reads_global_session_patch(
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
        patch_path = write_session_diff(get_session_dir(paths, "session-626"), patch_text)

        result = DiffService(paths, session_id="session-626").collect()

        assert result.status == DiffStatus.OK
        assert result.session_id == "session-626"
        assert result.diff_text == patch_text
        assert result.artifact_path == patch_path

    def test_collect_with_project_identity_and_no_global_session_returns_session_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """An absent global session returns not found without creating a directory."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        save_project_identity(repository / ".dovo" / "project.json", identity)
        paths = workspace_paths_factory(repository, None)

        result = DiffService(paths, session_id="session-626").collect()

        session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "session-626"
        assert result.status == DiffStatus.SESSION_NOT_FOUND
        assert not session_dir.exists()

    def test_collect_without_session_id_discovers_most_recently_modified_session(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] DiffService.collect: session_id omitted -> resolves the most recently modified session directory under sessions_dir, not the first alphabetically."""
        paths = workspace_paths_factory(tmp_path, None)
        older = get_session_dir(paths, "aaa-older")
        newer = get_session_dir(paths, "zzz-newer")
        write_session_diff(older, "diff --git a/old.txt b/old.txt\n")
        write_session_diff(newer, "diff --git a/new.txt b/new.txt\n")

        result = DiffService(paths).collect()

        assert result.status == DiffStatus.OK
        assert result.session_id == "zzz-newer"

    def test_collect_without_session_id_and_no_sessions_returns_session_not_found(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/integration] DiffService.collect: session_id omitted and no session directories exist -> SESSION_NOT_FOUND with no session_id set."""
        result = DiffService(workspace_paths_factory(tmp_path, None)).collect()

        assert result.status == DiffStatus.SESSION_NOT_FOUND
        assert result.session_id is None
