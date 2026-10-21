"""Shared workspace fixtures for dovo.core.status tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
from tests.harness import WorkspaceBuilder
from tests.harness.status_workspace import DEFAULT_STATUS_CONFIG, BuildStatusWorkspace


@pytest.fixture
def status_workspace(tmp_path: Path) -> BuildStatusWorkspace:
    """Return a builder of a git workspace without catalog templates.

    The builder writes config as config.json (JSON for a dict, raw text for a str) or omits the file when None.
    """

    def build(
        *,
        branch: str = "feature-status",
        database: bool = True,
        config: dict[str, object] | str | None = DEFAULT_STATUS_CONFIG,
    ) -> Path:
        builder = WorkspaceBuilder(tmp_path / "workspace").with_git(branch=branch).without_catalog_templates()
        if not database:
            builder = builder.without_database()
        if config is None:
            builder = builder.without_config()
        workspace = builder.build()
        if isinstance(config, str):
            Filesystem.atomic_write_text(Filesystem(workspace).config_file, config)
        elif config is not None:
            Filesystem.atomic_write_json(Filesystem(workspace).config_file, config)
        return workspace

    return build
