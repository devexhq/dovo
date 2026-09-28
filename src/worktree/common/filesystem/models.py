from __future__ import annotations

from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from worktree.common.lock import resolve_lock_file_path

if TYPE_CHECKING:
    # Annotation-only: from __future__ import annotations means this is never evaluated
    # at runtime, so it carries zero real common -> core coupling (ARCH-001).
    from worktree.core.catalog.models import CatalogTier


class YamlFile(BaseModel):
    """Container for parsed YAML file metadata and content."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    path: Path
    namespace: str | None = None
    error: str | None = None
    parsed: Any | None = None
    content: str | None = ""
    checksum: str | None = None
    file_size: int | None = None


class RepositoryPaths(BaseModel):
    """Repository-local paths discovered once per Filesystem instance."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    root_dir: Path
    worktree_dir: Path
    config_file: Path
    catalog_dir: Path
    catalog_steps_dir: Path
    catalog_blueprints_dir: Path
    sandboxes_dir: Path
    lock_file: Path
    gitignore_file: Path

    @classmethod
    def from_root(cls, root_dir: Path) -> RepositoryPaths:
        """Construct repository-local paths from a resolved repository root."""
        canonical_root = root_dir.expanduser().resolve()
        wt = canonical_root if canonical_root.name == ".worktree" else canonical_root / ".worktree"
        root_path = canonical_root.parent if canonical_root.name == ".worktree" else canonical_root

        return cls(
            root_dir=root_path,
            worktree_dir=wt,
            config_file=wt / "config.json",
            catalog_dir=wt / "catalog",
            catalog_steps_dir=wt / "catalog" / "steps",
            catalog_blueprints_dir=wt / "catalog" / "blueprints",
            sandboxes_dir=wt / "sandboxes",
            lock_file=resolve_lock_file_path(wt),
            gitignore_file=root_path / ".gitignore",
        )


class GlobalPaths(BaseModel):
    """Paths for global ~/.worktree hierarchy."""

    model_config = ConfigDict(extra="forbid", strict=True)

    root: Path
    global_dir: Path
    global_catalog_dir: Path
    user_dir: Path
    user_catalog_dir: Path
    data_dir: Path
    storage_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> GlobalPaths:
        """Construct the canonical global Worktree path hierarchy."""
        canonical_root = root.expanduser().resolve()
        return cls(
            root=canonical_root,
            global_dir=canonical_root / "global",
            global_catalog_dir=canonical_root / "global" / "catalog",
            user_dir=canonical_root / "user",
            user_catalog_dir=canonical_root / "user" / "catalog",
            data_dir=canonical_root / "data",
            storage_dir=canonical_root / "storage",
        )


class WorkspacePaths(BaseModel):
    """Immutable command-invocation snapshot of every ambient workspace path."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, arbitrary_types_allowed=True)

    root_dir: Path
    worktree_dir: Path
    config_file: Path
    catalog_dir: Path
    catalog_steps_dir: Path
    catalog_blueprints_dir: Path
    sandboxes_dir: Path
    lock_file: Path
    gitignore_file: Path
    catalog_templates_dir: Traversable

    global_paths: GlobalPaths
    database_file: Path

    project_id: str | None
    runtime_root: Path
    logs_dir: Path
    sessions_dir: Path
    artifacts_dir: Path
    tmp_dir: Path

    def session_dir(self, session_id: str) -> Path:
        """Return path to a specific session directory."""
        return self.sessions_dir / session_id

    def sandbox_dir(self, sandbox_id: str) -> Path:
        """Return path to a specific sandbox directory."""
        return self.sandboxes_dir / sandbox_id

    def catalog_dir_for(self, tier: CatalogTier) -> Path:
        """Return the disk-backed catalog directory root for tier; raises ValueError for CatalogTier.PACKAGED."""
        if tier == "repo":
            return self.catalog_dir
        if tier == "user":
            return self.global_paths.user_catalog_dir
        if tier == "global":
            return self.global_paths.global_catalog_dir
        raise ValueError(f"Tier '{tier}' is not disk-backed and has no tier root.")
