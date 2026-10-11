# tests/cli/session/conftest.py
"""Shared workspace fixture for dovo session CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def session_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized git workspace (config, db, catalog) for dovo session CLI tests."""
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
