"""Contract tests for the Filesystem entrypoint: repository-local paths, dynamic lookup and bound I/O helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.filesystem.filesystem import Filesystem
from dovo.common.filesystem.models import YamlFile


class FilesystemPathPropertiesTests:
    def test_path_properties_delegate_to_resolved_repository_paths(self, tmp_path: Path) -> None:
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
        fs = Filesystem(tmp_path)
        first = fs.repository_paths
        second = fs.repository_paths
        assert first is second

    def test_worktree_dir_joins_the_identifier_onto_worktrees_dir(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        assert fs.worktree_dir("dovo_1") == fs.worktrees_dir / "dovo_1"

    def test_rel_to_root_returns_path_relative_to_workspace_root(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        inside = fs.root_dir / "sub" / "file.txt"
        outside = tmp_path.parent / "elsewhere.txt"

        assert fs.rel_to_root(inside) == Path("sub") / "file.txt"
        assert fs.rel_to_root(outside) == outside


class FilesystemDynamicAttributeLookupTests:
    def test_unknown_attribute_raises_attribute_error(self, tmp_path: Path) -> None:
        """Raise AttributeError naming the Filesystem type for an attribute absent from it and RepositoryPaths."""
        fs = Filesystem(tmp_path)
        with pytest.raises(AttributeError, match="Filesystem"):
            _ = fs.definitely_not_a_real_attribute


class FilesystemReprTests:
    def test_repr_includes_resolved_root_dir(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        assert str(fs.root_dir) in repr(fs)


class FilesystemBoundIoHelperTests:
    def test_write_text_creates_file_with_given_content(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        target = tmp_path / "note.txt"
        fs.write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_write_json_creates_file_with_given_data(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        target = tmp_path / "data.json"
        fs.write_json(target, {"key": "value"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"key": "value"}

    def test_delete_file_returns_true_when_file_existed_and_false_otherwise(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        target = tmp_path / "to-delete.txt"
        target.write_text("x", encoding="utf-8")

        assert fs.delete_file(target) is True
        assert not target.exists()
        assert fs.delete_file(target) is False

    def test_read_yaml_parses_file_into_yaml_file_model(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        target = tmp_path / "item.yml"
        target.write_text("name: sample\n", encoding="utf-8")

        result = fs.read_yaml(target)

        assert isinstance(result, YamlFile)
        assert result.parsed == {"name": "sample"}
        assert result.error is None

    def test_scan_yaml_returns_sorted_yaml_files_from_directory(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        (tmp_path / "b.yml").write_text("name: b\n", encoding="utf-8")
        (tmp_path / "a.yaml").write_text("name: a\n", encoding="utf-8")
        (tmp_path / "skip.txt").write_text("ignored", encoding="utf-8")

        results = fs.scan_yaml(tmp_path)

        assert [entry.path.name for entry in results] == ["a.yaml", "b.yml"]

    def test_is_git_repo_detects_dot_git_directory(self, tmp_path: Path) -> None:
        """Return True for a directory containing .git, False otherwise; default to root_dir when no path is given."""
        fs = Filesystem(tmp_path)
        assert fs.is_git_repo(tmp_path) is False

        (tmp_path / ".git").mkdir()
        assert fs.is_git_repo(tmp_path) is True
        assert fs.is_git_repo() is True

    def test_checksum_returns_sha256_hex_digest(self, tmp_path: Path) -> None:
        import hashlib

        fs = Filesystem(tmp_path)
        assert fs.checksum("payload") == hashlib.sha256(b"payload").hexdigest()


class FilesystemStaticHelperTests:
    def test_find_root_delegates_to_find_dovo_root(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        (repo_root / ".git").mkdir(parents=True)
        assert Filesystem.find_root(repo_root) == Filesystem(repo_root).root_dir

    def test_is_git_repository_static_matches_instance_method(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert Filesystem.is_git_repository(tmp_path) is True

    def test_atomic_write_text_and_json_static_helpers_write_to_disk(self, tmp_path: Path) -> None:
        text_target = tmp_path / "static.txt"
        json_target = tmp_path / "static.json"

        Filesystem.atomic_write_text(text_target, "static-content")
        Filesystem.atomic_write_json(json_target, {"a": 1})

        assert text_target.read_text(encoding="utf-8") == "static-content"
        assert json.loads(json_target.read_text(encoding="utf-8")) == {"a": 1}

    def test_compute_checksum_static_matches_instance_method(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        assert Filesystem.compute_checksum("payload") == fs.checksum("payload")

    def test_read_yaml_file_and_scan_yaml_directory_static_helpers_match_instance_methods(self, tmp_path: Path) -> None:
        target = tmp_path / "item.yml"
        target.write_text("name: sample\n", encoding="utf-8")

        fs = Filesystem(tmp_path)
        assert Filesystem.read_yaml_file(target).parsed == fs.read_yaml(target).parsed
        assert [f.name for f in Filesystem.scan_yaml_directory(tmp_path)] == [f.name for f in fs.scan_yaml(tmp_path)]


class FilesystemAtomicWriteBytesTests:
    def test_non_utf8_bytes_and_crlf_are_written_verbatim_replacing_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "out.patch"
        target.parent.mkdir()
        target.write_bytes(b"old")

        Filesystem.atomic_write_bytes(target, b"\xff\xfe\r\nx")

        assert target.read_bytes() == b"\xff\xfe\r\nx"
        assert not target.with_name("out.patch.tmp").exists()

    def test_unwritable_parent_raises_oserror_and_leaves_no_tmp_sibling(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("file", encoding="utf-8")

        with pytest.raises(OSError):
            Filesystem.atomic_write_bytes(blocker / "out.patch", b"data")

        assert sorted(path.name for path in tmp_path.iterdir()) == ["blocker"]
