"""Shared workspace fixture for wt artifacts CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def artifacts_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace (git, config, db, catalog) for wt artifacts CLI tests."""
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
