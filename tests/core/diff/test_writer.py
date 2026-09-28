"""Tests for session artifact directory resolution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from worktree.common.filesystem import WorkspacePaths
from worktree.core.diff.writer import get_session_dir
from worktree.core.project.models import ProjectIdentity
from worktree.core.project.services.identity import save_project_identity

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class SessionWriterTests:
    """Integration tests for creating project-aware session directories."""

    def test_get_session_dir_with_project_identity_creates_global_session_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """An identified project creates its session directory in global storage."""
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        monkeypatch.setenv("WORKTREE_HOME", str(global_root))
        save_project_identity(repository / ".worktree" / "project.json", identity)

        session_dir = get_session_dir(workspace_paths_factory(repository, None), "session-626")

        expected_session_dir = global_root / "storage" / "projects" / "project-626" / "sessions" / "session-626"
        assert session_dir == expected_session_dir
        assert session_dir.is_dir()
        assert not (repository / ".worktree" / "sessions" / "session-626").exists()
