"""Fluent workspace data builder for Dovo CLI test suite."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo
from dovo.core.catalog.services.seeder import seed_all_catalog_templates
from dovo.core.config.generator import generate_default_config
from dovo.core.db.migrations import init_database
from dovo.core.project.services.identity import generate_project_identity, save_project_identity
from dovo.core.project.services.storage import resolve_workspace_paths


class WorkspaceBuilder:
    """Fluent builder for scaffolding test workspaces on the filesystem."""

    def __init__(self, root: Path | None = None) -> None:
        self._root: Path | None = root
        self._project_name: str | None = None
        self._scaffold_config: bool = True
        self._config_overwrite: bool = True
        self._scaffold_database: bool = True
        self._scaffold_catalog: bool = True
        self._catalog_force: bool = True
        self._init_git: bool = False
        self._config_data: dict[str, Any] | None = None
        self._git_branch: str = "main"
        self._git_user_name: str = "Test User"
        self._git_user_email: str = "test@example.com"

    def with_project_name(self, name: str) -> WorkspaceBuilder:
        """Set project name for generated configuration."""
        self._project_name = name
        return self

    def with_config(
        self,
        *,
        data: dict[str, Any] | None = None,
        overwrite: bool = True,
    ) -> WorkspaceBuilder:
        """Enable config generation with optional explicit payload."""
        self._scaffold_config = True
        self._config_data = data
        self._config_overwrite = overwrite
        return self

    def without_config(self) -> WorkspaceBuilder:
        """Disable config.json generation."""
        self._scaffold_config = False
        return self

    def with_database(self) -> WorkspaceBuilder:
        """Enable SQLite database migration."""
        self._scaffold_database = True
        return self

    def without_database(self) -> WorkspaceBuilder:
        """Disable SQLite database creation."""
        self._scaffold_database = False
        return self

    def with_catalog_templates(self, *, force: bool = True) -> WorkspaceBuilder:
        """Enable catalog templates seeding."""
        self._scaffold_catalog = True
        self._catalog_force = force
        return self

    def without_catalog_templates(self) -> WorkspaceBuilder:
        """Disable catalog templates seeding."""
        self._scaffold_catalog = False
        return self

    def with_git(
        self,
        *,
        branch: str = "main",
        user_name: str = "Test User",
        user_email: str = "test@example.com",
    ) -> WorkspaceBuilder:
        """Enable Git repository initialization."""
        self._init_git = True
        self._git_branch = branch
        self._git_user_name = user_name
        self._git_user_email = user_email
        return self

    def build(self) -> Path:
        """Scaffold and return the prepared workspace directory root."""
        if self._root is not None:
            workspace_root = self._root.resolve()
            workspace_root.mkdir(parents=True, exist_ok=True)
        else:
            workspace_root = Path(tempfile.mkdtemp(prefix="wt_workspace_")).resolve()

        dot_dovo = workspace_root / ".dovo"
        dot_dovo.mkdir(parents=True, exist_ok=True)
        bootstrap_dovo(dot_dovo)

        (dot_dovo / "sandboxes").mkdir(parents=True, exist_ok=True)

        if self._init_git:
            self._scaffold_git_repository(workspace_root)

        if self._scaffold_config:
            self._scaffold_workspace_config(workspace_root, dot_dovo)

        if self._scaffold_database:
            identity = generate_project_identity()
            save_project_identity(dot_dovo / "project.json", identity)
            paths = resolve_workspace_paths(RepositoryPaths.from_root(workspace_root), resolve_global_paths(None))
            init_database(paths.database_file)

        if self._scaffold_catalog:
            paths = resolve_workspace_paths(RepositoryPaths.from_root(workspace_root), resolve_global_paths(None))
            seed_all_catalog_templates(paths, force=self._catalog_force)

        return workspace_root

    def _scaffold_git_repository(self, workspace_root: Path) -> None:
        subprocess.run(
            ["git", "init", "-b", self._git_branch],
            cwd=workspace_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", self._git_user_name],
            cwd=workspace_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", self._git_user_email],
            cwd=workspace_root,
            check=True,
            capture_output=True,
            text=True,
        )

        readme_path = workspace_root / "README.md"
        if not readme_path.exists():
            readme_path.write_text("# Test Repo\n", encoding="utf-8")

        gitignore_path = workspace_root / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text("/.dovo/\n", encoding="utf-8")

        subprocess.run(
            ["git", "add", "README.md", ".gitignore"],
            cwd=workspace_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=workspace_root,
            check=True,
            capture_output=True,
            text=True,
        )

    def _scaffold_workspace_config(self, workspace_root: Path, dot_dovo: Path) -> None:
        config_path = dot_dovo / "config.json"
        if self._config_data is not None:
            if not self._config_overwrite and config_path.exists():
                return
            Filesystem.atomic_write_json(config_path, self._config_data)
        else:
            project_name = self._project_name or workspace_root.name
            generate_default_config(config_path, project_name=project_name, overwrite=self._config_overwrite)
