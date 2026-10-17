"""Fluent workspace data builder for Dovo CLI test suite."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo
from dovo.core.catalog.services.seeder import seed_all_catalog_templates
from dovo.core.config.generator import generate_default_config
from dovo.core.db.migrations import init_database
from dovo.core.project.services.identity import generate_project_identity, save_project_identity
from dovo.core.project.services.storage import build_workspace_paths

_TEMPLATE_DIRECTORY: tempfile.TemporaryDirectory[str] | None = None
_TEMPLATE_DATABASE_PATH: Path | None = None
_GIT_TEMPLATES: dict[tuple[str, str, str, tuple[tuple[str, str], ...] | None], Path] = {}


def _get_template_directory() -> Path:
    """Return an ephemeral directory for the current worker process holding template assets."""
    global _TEMPLATE_DIRECTORY
    if _TEMPLATE_DIRECTORY is None:
        _TEMPLATE_DIRECTORY = tempfile.TemporaryDirectory(prefix="dovo_test_templates_")
    return Path(_TEMPLATE_DIRECTORY.name)


def init_test_database(database_file: Path) -> Path:
    """Initialize database_file with a freshly migrated SQLite schema cloned from a cached template."""
    global _TEMPLATE_DATABASE_PATH
    if database_file.exists():
        init_database(database_file)
        return database_file

    if _TEMPLATE_DATABASE_PATH is None or not _TEMPLATE_DATABASE_PATH.exists():
        template_directory = _get_template_directory()
        template_file = template_directory / "template_dovo.db"
        init_database(template_file)
        _TEMPLATE_DATABASE_PATH = template_file

    database_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_TEMPLATE_DATABASE_PATH, database_file)
    return database_file


def _create_git_template(
    branch: str,
    user_name: str,
    user_email: str,
    initial_files: Mapping[str, str] | None,
) -> Path:
    """Create and populate a template Git repository in the worker template directory."""
    template_directory = _get_template_directory()
    template_repository = Path(tempfile.mkdtemp(prefix="git_template_", dir=template_directory))
    _git(template_repository, "init", "-b", branch)
    _git(template_repository, "config", "user.name", user_name)
    _git(template_repository, "config", "user.email", user_email)
    if initial_files:
        for relative_path, content in initial_files.items():
            file_path = template_repository / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        _git(template_repository, "add", *initial_files)
        _git(template_repository, "commit", "-m", "Initial commit")
    return template_repository


def _get_git_template(
    branch: str,
    user_name: str,
    user_email: str,
    initial_files: Mapping[str, str] | None,
) -> Path:
    """Return a cached template Git repository for the given configuration."""
    files_key = tuple(sorted(initial_files.items())) if initial_files else None
    cache_key = (branch, user_name, user_email, files_key)
    template_repository = _GIT_TEMPLATES.get(cache_key)
    if template_repository is None or not template_repository.exists():
        template_repository = _create_git_template(branch, user_name, user_email, initial_files)
        _GIT_TEMPLATES[cache_key] = template_repository
    return template_repository


def _init_git_repository_direct(
    root: Path,
    branch: str,
    user_name: str,
    user_email: str,
    initial_files: Mapping[str, str] | None,
) -> None:
    """Initialize a Git repository directly in root without using a cached template."""
    _git(root, "init", "-b", branch)
    _git(root, "config", "user.name", user_name)
    _git(root, "config", "user.email", user_email)
    if not initial_files:
        return

    for relative_path, content in initial_files.items():
        file_path = root / relative_path
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
    _git(root, "add", *initial_files)
    _git(root, "commit", "-m", "Initial commit")


def init_git_repository(
    root: Path,
    *,
    branch: str = "main",
    user_name: str = "Test User",
    user_email: str = "test@example.com",
    initial_files: Mapping[str, str] | None = None,
) -> None:
    """Run git init in root with a committable identity, using a cached template when available.

    initial_files maps a path relative to root to its content; a file that already exists keeps its content.
    """
    has_git_directory = (root / ".git").exists()
    has_conflicting_files = bool(
        initial_files and any((root / relative_path).exists() for relative_path in initial_files)
    )
    if has_git_directory or has_conflicting_files:
        _init_git_repository_direct(root, branch, user_name, user_email, initial_files)
        return

    template_repository = _get_git_template(branch, user_name, user_email, initial_files)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_repository, root, dirs_exist_ok=True)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class WorkspaceBuilder:
    """Fluent builder for scaffolding test workspaces on the filesystem."""

    def __init__(self, root: Path | None = None) -> None:
        self._root: Path | None = root
        self._scaffold_config: bool = True
        self._scaffold_database: bool = True
        self._scaffold_identity: bool = True
        self._scaffold_catalog: bool = True
        self._init_git: bool = False
        self._config_data: dict[str, object] | None = None
        self._git_branch: str = "main"
        self._git_user_name: str = "Test User"
        self._git_user_email: str = "test@example.com"

    def with_config(self, *, data: dict[str, object] | None = None) -> WorkspaceBuilder:
        """Enable config generation with optional explicit payload."""
        self._scaffold_config = True
        self._config_data = data
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

    def without_identity(self) -> WorkspaceBuilder:
        """Skip writing project.json so the built workspace is uninitialized."""
        self._scaffold_identity = False
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
            workspace_root = Path(tempfile.mkdtemp(prefix="dovo_workspace_")).resolve()

        dot_dovo = workspace_root / ".dovo"
        dot_dovo.mkdir(parents=True, exist_ok=True)
        bootstrap_dovo(dot_dovo)

        (dot_dovo / "worktrees").mkdir(parents=True, exist_ok=True)

        if self._init_git:
            self._scaffold_git_repository(workspace_root)

        if self._scaffold_config:
            self._scaffold_workspace_config(workspace_root, dot_dovo)

        identity = generate_project_identity()
        if self._scaffold_identity:
            save_project_identity(dot_dovo / "project.json", identity)

        paths = build_workspace_paths(
            RepositoryPaths.from_root(workspace_root), resolve_global_paths(None), identity.id
        )
        if self._scaffold_database:
            init_test_database(paths.database_file)

        if self._scaffold_catalog:
            seed_all_catalog_templates(paths, force=True)

        return workspace_root

    def _scaffold_git_repository(self, workspace_root: Path) -> None:
        init_git_repository(
            workspace_root,
            branch=self._git_branch,
            user_name=self._git_user_name,
            user_email=self._git_user_email,
            initial_files={"README.md": "# Test Repo\n", ".gitignore": "/.dovo/\n"},
        )

    def _scaffold_workspace_config(self, workspace_root: Path, dot_dovo: Path) -> None:
        config_path = dot_dovo / "config.json"
        if self._config_data is not None:
            Filesystem.atomic_write_json(config_path, self._config_data)
        else:
            generate_default_config(config_path, project_name=workspace_root.name, overwrite=True)
