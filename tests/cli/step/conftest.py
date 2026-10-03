"""Shared fixtures for dovo step CLI integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config


@pytest.fixture(autouse=True)
def _setup_workspace_config(isolated_workspace: Path) -> None:
    """Ensure workspace contains valid config.json for CLI context resolution."""
    config_path = isolated_workspace / ".dovo" / "config.json"
    payload = build_default_config("demo-workspace")
    Filesystem.atomic_write_json(config_path, payload)
