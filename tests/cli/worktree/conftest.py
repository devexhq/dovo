"""Shared workspace fixture for worktree CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace (git, config, db, catalog) for worktree CLI tests."""
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
