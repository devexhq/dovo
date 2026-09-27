"""Shared workspace fixture for wt logs CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def logs_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized git workspace (config, db) for wt logs CLI tests."""
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
