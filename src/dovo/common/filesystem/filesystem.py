from __future__ import annotations

from functools import cached_property
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from dovo.common.filesystem.models import RepositoryPaths, YamlFile
from dovo.common.filesystem.services.git import is_git_repository as _is_git_repository
from dovo.common.filesystem.services.operations import (
    atomic_write_json as _atomic_write_json,
    atomic_write_text as _atomic_write_text,
    compute_content_checksum as _compute_content_checksum,
    delete_file as _delete_file,
)
from dovo.common.filesystem.services.paths import (
    find_dovo_root as _find_dovo_root,
    get_catalog_templates_dir as _get_catalog_templates_dir,
)
from dovo.common.filesystem.services.yaml import (
    read_yaml_file as _read_yaml_file,
    scan_yaml_directory as _scan_yaml_directory,
)


class Filesystem:
    """Unified entrypoint for repository-local workspace paths and atomic I/O."""

    def __init__(self, start: Path | None = None) -> None:
        """Bind this Filesystem instance to a repository start path, discovered lazily."""
        self._start = start

    @cached_property
    def repository_paths(self) -> RepositoryPaths:
        """Discover the repository once for this Filesystem instance."""
        return RepositoryPaths.from_root(_find_dovo_root(self._start))

    @property
    def root_dir(self) -> Path:
        """Workspace root directory."""
        return self.repository_paths.root_dir

    @property
    def dovo_dir(self) -> Path:
        """Hidden .dovo workspace state directory."""
        return self.repository_paths.dovo_dir

    @property
    def config_file(self) -> Path:
        """Path to config.json."""
        return self.repository_paths.config_file

    @property
    def catalog_dir(self) -> Path:
        """Path to catalog root directory."""
        return self.repository_paths.catalog_dir

    @property
    def catalog_steps_dir(self) -> Path:
        """Path to catalog steps directory."""
        return self.repository_paths.catalog_steps_dir

    @property
    def catalog_blueprints_dir(self) -> Path:
        """Path to catalog blueprints directory."""
        return self.repository_paths.catalog_blueprints_dir

    @property
    def worktrees_dir(self) -> Path:
        """Path to worktrees directory."""
        return self.repository_paths.worktrees_dir

    @property
    def lock_file(self) -> Path:
        """Path to workspace lock file."""
        return self.repository_paths.lock_file

    @property
    def gitignore_file(self) -> Path:
        """Path to workspace .gitignore file."""
        return self.repository_paths.gitignore_file

    @property
    def catalog_templates_dir(self) -> Traversable:
        """Traversable resource path to bundled catalog templates."""
        return _get_catalog_templates_dir()

    def worktree_dir(self, worktree_id: str) -> Path:
        """Return path to a specific worktree directory."""
        return self.repository_paths.worktrees_dir / worktree_id

    def rel_to_root(self, path: Path | str) -> Path:
        """Return path relative to workspace root."""
        try:
            return Path(path).resolve().relative_to(self.repository_paths.root_dir)
        except ValueError:
            return Path(path)

    def __getattr__(self, name: str) -> Any:
        """Delegate fallback attribute lookups directly to self.repository_paths."""
        repository_paths = self.repository_paths
        if hasattr(repository_paths, name):
            return getattr(repository_paths, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __repr__(self) -> str:
        return f"Filesystem(root={self.repository_paths.root_dir!r})"

    # Bound instance methods
    def write_text(self, path: Path, text: str) -> None:
        """Write text content atomically with UTF-8 encoding."""
        _atomic_write_text(path, text)

    def write_json(self, path: Path, data: dict[str, Any]) -> None:
        """Write JSON content atomically with indent=2, UTF-8, and trailing newline."""
        _atomic_write_json(path, data)

    def delete_file(self, path: Path) -> bool:
        """Delete a file if it exists. Returns True if the file existed before deletion."""
        return _delete_file(path)

    def read_yaml(self, path: Path) -> YamlFile:
        """Read and parse a YAML file into a typed YamlFile model."""
        return _read_yaml_file(path)

    def scan_yaml(self, directory: Path, *, suffixes: tuple[str, ...] = (".yml", ".yaml")) -> list[YamlFile]:
        """Scan a directory recursively for matching YAML files, sorted by path."""
        return _scan_yaml_directory(directory, suffixes=suffixes)

    def is_git_repo(self, path: Path | None = None) -> bool:
        """Check whether the given directory contains a .git directory or file."""
        target = path if path is not None else self.repository_paths.root_dir
        return _is_git_repository(target)

    def checksum(self, content: str) -> str:
        """Compute SHA-256 hex digest of string content."""
        return _compute_content_checksum(content)

    # Static helpers for standalone execution
    @staticmethod
    def find_root(start: Path | None = None) -> Path:
        """Find the root directory of a Dovo workspace or git repository."""
        return _find_dovo_root(start)

    @staticmethod
    def is_git_repository(path: Path) -> bool:
        """Check whether the given directory contains a .git directory or file."""
        return _is_git_repository(path)

    @staticmethod
    def atomic_write_text(path: Path, text: str) -> None:
        """Write text content atomically with UTF-8 encoding."""
        _atomic_write_text(path, text)

    @staticmethod
    def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
        """Write JSON content atomically with indent=2, UTF-8, and trailing newline."""
        _atomic_write_json(path, data)

    @staticmethod
    def compute_checksum(content: str) -> str:
        """Compute SHA-256 hex digest of string content."""
        return _compute_content_checksum(content)

    @staticmethod
    def read_yaml_file(file_path: Path) -> YamlFile:
        """Read and parse a YAML file into a typed YamlFile model."""
        return _read_yaml_file(file_path)

    @staticmethod
    def scan_yaml_directory(directory: Path, *, suffixes: tuple[str, ...] = (".yml", ".yaml")) -> list[YamlFile]:
        """Scan a directory recursively for matching YAML files, sorted by path."""
        return _scan_yaml_directory(directory, suffixes=suffixes)
