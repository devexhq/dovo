"""Contract tests for the Filesystem entrypoint: repository-local paths, dynamic lookup and bound I/O helpers."""

from __future__ import annotations

import hashlib
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


class FilesystemReprTests:
    def test_repr_includes_resolved_root_dir(self, tmp_path: Path) -> None:
        fs = Filesystem(tmp_path)
        assert str(fs.root_dir) in repr(fs)


class FilesystemBoundIoHelperTests:
    def test_is_git_repo_detects_dot_git_directory(self, tmp_path: Path) -> None:
        """Return True for a directory containing .git, False otherwise; default to root_dir when no path is given."""
        fs = Filesystem(tmp_path)
        assert fs.is_git_repo(tmp_path) is False

        (tmp_path / ".git").mkdir()
        assert fs.is_git_repo(tmp_path) is True
        assert fs.is_git_repo() is True


class FilesystemStaticHelperTests:
    def test_atomic_write_text_and_json_static_helpers_write_to_disk(self, tmp_path: Path) -> None:
        text_target = tmp_path / "static.txt"
        json_target = tmp_path / "static.json"

        Filesystem.atomic_write_text(text_target, "static-content")
        Filesystem.atomic_write_json(json_target, {"a": 1})

        assert text_target.read_text(encoding="utf-8") == "static-content"
        assert json.loads(json_target.read_text(encoding="utf-8")) == {"a": 1}

    def test_compute_checksum_returns_sha256_hex_digest(self) -> None:
        assert Filesystem.compute_checksum("payload") == hashlib.sha256(b"payload").hexdigest()

    def test_read_yaml_file_parses_file_into_yaml_file_model(self, tmp_path: Path) -> None:
        target = tmp_path / "item.yml"
        target.write_text("name: sample\n", encoding="utf-8")

        result = Filesystem.read_yaml_file(target)

        assert isinstance(result, YamlFile)
        assert result.parsed == {"name": "sample"}
        assert result.error is None

    def test_scan_yaml_directory_returns_sorted_yaml_files_from_directory(self, tmp_path: Path) -> None:
        (tmp_path / "b.yml").write_text("name: b\n", encoding="utf-8")
        (tmp_path / "a.yaml").write_text("name: a\n", encoding="utf-8")
        (tmp_path / "skip.txt").write_text("ignored", encoding="utf-8")

        results = Filesystem.scan_yaml_directory(tmp_path)

        assert [entry.path.name for entry in results] == ["a.yaml", "b.yml"]


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
