# tests/cli/resume/conftest.py
"""Shared workspace fixture for dovo resume CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def resume_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized git workspace (config, db, catalog) for dovo resume CLI tests."""
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()
