"""Shared helper resolving WorkspacePaths for tests that need an initialized project identity."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import DovoDb
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.storage import resolve_workspace_paths

_TEST_PROJECT_ID = "test-project"


def initialized_workspace_paths(root: Path, global_root: Path | None = None) -> WorkspacePaths:
    """Resolve WorkspacePaths for root, writing a deterministic 'test-project' identity first when none exists."""
    identity_file = root / ".dovo" / "project.json"
    if not identity_file.exists():
        identity = ProjectIdentity(id=_TEST_PROJECT_ID, created_at=datetime(2026, 1, 1, tzinfo=UTC))
        Filesystem.atomic_write_json(identity_file, identity.model_dump(mode="json"))

    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(global_root))


def initialized_workspace_db(root: Path) -> DovoDb:
    """Construct a DovoDb bound to root's resolved database file and project id."""
    paths = initialized_workspace_paths(root)
    return DovoDb(database_file=paths.database_file, project_id=paths.project_id)
