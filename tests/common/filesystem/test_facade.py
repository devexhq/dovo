"""Contract tests for the Filesystem facade: repository-local paths, dynamic attribute lookup, and bound I/O helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.filesystem.facade import Filesystem
from dovo.common.filesystem.models import YamlFile


class FilesystemPathPropertiesTests:
    def test_path_properties_delegate_to_resolved_repository_paths(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem path properties (root_dir, dovo_dir, config_file, catalog_dir, catalog_steps_dir, catalog_blueprints_dir, worktrees_dir, lock_file, gitignore_file): each equals the corresponding field on fs.repository_paths for the same root."""
        fs = Filesystem(tmp_path)
        resolved = fs.repository_paths

        assert fs.root_dir == resolved.root_dir
        assert fs.dovo_dir == resolved.dovo_dir
        assert fs.config_file == resolved.config_file
        assert fs.catalog_dir == resolved.catalog_dir
        assert fs.catalog_steps_dir == resolved.catalog_steps_dir
        assert fs.catalog_blueprints_dir == resolved.catalog_blueprints_dir
        assert fs.worktrees_dir == resolved.worktrees_dir
        assert fs.lock_file == resolved.lock_file
        assert fs.gitignore_file == resolved.gitignore_file

    def test_repository_paths_is_cached_across_repeated_access(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.repository_paths: repeated access on one instance returns the identical cached RepositoryPaths object, not a freshly re-resolved one."""
        fs = Filesystem(tmp_path)
        first = fs.repository_paths
        second = fs.repository_paths
        assert first is second

    def test_worktree_dir_joins_the_identifier_onto_worktrees_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.worktree_dir: returns worktrees_dir/<id>."""
        fs = Filesystem(tmp_path)
        assert fs.worktree_dir("dovo_1") == fs.worktrees_dir / "dovo_1"

    def test_rel_to_root_returns_path_relative_to_workspace_root(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.rel_to_root: a path under root_dir is returned relative to it; a path outside root_dir is returned unchanged."""
        fs = Filesystem(tmp_path)
        inside = fs.root_dir / "sub" / "file.txt"
        outside = tmp_path.parent / "elsewhere.txt"

        assert fs.rel_to_root(inside) == Path("sub") / "file.txt"
        assert fs.rel_to_root(outside) == outside


class FilesystemDynamicAttributeLookupTests:
    def test_unknown_attribute_raises_attribute_error(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.__getattr__: an attribute absent from both Filesystem and RepositoryPaths raises AttributeError naming the Filesystem type."""
        fs = Filesystem(tmp_path)
        with pytest.raises(AttributeError, match="Filesystem"):
            _ = fs.definitely_not_a_real_attribute


class FilesystemReprTests:
    def test_repr_includes_resolved_root_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.__repr__: rendered string contains the resolved root_dir."""
        fs = Filesystem(tmp_path)
        assert str(fs.root_dir) in repr(fs)


class FilesystemBoundIoHelperTests:
    def test_write_text_creates_file_with_given_content(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.write_text: target file exists on disk afterward with the exact written content."""
        fs = Filesystem(tmp_path)
        target = tmp_path / "note.txt"
        fs.write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_write_json_creates_file_with_given_data(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.write_json: target file exists on disk afterward and parses back to the exact written dict."""
        fs = Filesystem(tmp_path)
        target = tmp_path / "data.json"
        fs.write_json(target, {"key": "value"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"key": "value"}

    def test_delete_file_returns_true_when_file_existed_and_false_otherwise(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.delete_file: returns True and removes an existing file; returns False for a path that never existed."""
        fs = Filesystem(tmp_path)
        target = tmp_path / "to-delete.txt"
        target.write_text("x", encoding="utf-8")

        assert fs.delete_file(target) is True
        assert not target.exists()
        assert fs.delete_file(target) is False

    def test_read_yaml_parses_file_into_yaml_file_model(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.read_yaml: returns a YamlFile with parsed content for a well-formed YAML file."""
        fs = Filesystem(tmp_path)
        target = tmp_path / "item.yml"
        target.write_text("name: sample\n", encoding="utf-8")

        result = fs.read_yaml(target)

        assert isinstance(result, YamlFile)
        assert result.parsed == {"name": "sample"}
        assert result.error is None

    def test_scan_yaml_returns_sorted_yaml_files_from_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.scan_yaml: returns every matching .yml/.yaml file under the directory, sorted by path."""
        fs = Filesystem(tmp_path)
        (tmp_path / "b.yml").write_text("name: b\n", encoding="utf-8")
        (tmp_path / "a.yaml").write_text("name: a\n", encoding="utf-8")
        (tmp_path / "skip.txt").write_text("ignored", encoding="utf-8")

        results = fs.scan_yaml(tmp_path)

        assert [entry.path.name for entry in results] == ["a.yaml", "b.yml"]

    def test_is_git_repo_detects_dot_git_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.is_git_repo: True for a directory containing .git, False otherwise; defaults to checking root_dir when no path is given."""
        fs = Filesystem(tmp_path)
        assert fs.is_git_repo(tmp_path) is False

        (tmp_path / ".git").mkdir()
        assert fs.is_git_repo(tmp_path) is True
        assert fs.is_git_repo() is True

    def test_checksum_returns_sha256_hex_digest(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.checksum: returns the SHA-256 hex digest of the given string, matching hashlib directly."""
        import hashlib

        fs = Filesystem(tmp_path)
        assert fs.checksum("payload") == hashlib.sha256(b"payload").hexdigest()


class FilesystemStaticHelperTests:
    def test_find_root_delegates_to_find_dovo_root(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.find_root: static helper returns the same result as constructing a Filesystem for the same start path."""
        repo_root = tmp_path / "repo"
        (repo_root / ".git").mkdir(parents=True)
        assert Filesystem.find_root(repo_root) == Filesystem(repo_root).root_dir

    def test_is_git_repository_static_matches_instance_method(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.is_git_repository: static helper agrees with the bound is_git_repo instance method for the same path."""
        (tmp_path / ".git").mkdir()
        assert Filesystem.is_git_repository(tmp_path) is True

    def test_atomic_write_text_and_json_static_helpers_write_to_disk(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.atomic_write_text/atomic_write_json: static helpers write real files without requiring an instance."""
        text_target = tmp_path / "static.txt"
        json_target = tmp_path / "static.json"

        Filesystem.atomic_write_text(text_target, "static-content")
        Filesystem.atomic_write_json(json_target, {"a": 1})

        assert text_target.read_text(encoding="utf-8") == "static-content"
        assert json.loads(json_target.read_text(encoding="utf-8")) == {"a": 1}

    def test_compute_checksum_static_matches_instance_method(self, tmp_path: Path) -> None:
        """[tier-1/unit] Filesystem.compute_checksum: static helper returns the identical digest as the bound checksum method for the same content."""
        fs = Filesystem(tmp_path)
        assert Filesystem.compute_checksum("payload") == fs.checksum("payload")

    def test_read_yaml_file_and_scan_yaml_directory_static_helpers_match_instance_methods(self, tmp_path: Path) -> None:
        """[tier-1/integration] Filesystem.read_yaml_file/scan_yaml_directory: static helpers produce the same result as the bound read_yaml/scan_yaml methods."""
        target = tmp_path / "item.yml"
        target.write_text("name: sample\n", encoding="utf-8")

        fs = Filesystem(tmp_path)
        assert Filesystem.read_yaml_file(target).parsed == fs.read_yaml(target).parsed
        assert [f.name for f in Filesystem.scan_yaml_directory(tmp_path)] == [f.name for f in fs.scan_yaml(tmp_path)]
