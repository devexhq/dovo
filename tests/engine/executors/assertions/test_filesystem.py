"""Contract tests for engine/executors/assertions/filesystem.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree.engine.executors.assertions.filesystem import (
    evaluate_file_exists,
    evaluate_file_not_empty,
    evaluate_file_not_exists,
)


class EvaluateFileExistsTests:
    def test_existing_file_passes(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_exists: an existing file, given as a string or a list, yields no failures."""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")

        assert evaluate_file_exists("a.txt", tmp_path) == []
        assert evaluate_file_exists(["a.txt"], tmp_path) == []

    def test_missing_path_and_directory_each_fail(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_exists: a missing path and a directory each produce their own failure, in list order."""
        (tmp_path / "dir").mkdir()

        failures = evaluate_file_exists(["missing.txt", "dir"], tmp_path)

        assert failures == [
            "file_exists: path 'missing.txt' does not exist",
            "file_exists: path 'dir' is a directory, not a file",
        ]

    def test_path_escaping_the_root_fails_closed(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_exists: a traversal outside the root reports an escape failure even if the target exists."""
        root = tmp_path / "sandbox"
        root.mkdir()
        (tmp_path / "outside.txt").write_text("x", encoding="utf-8")

        assert evaluate_file_exists("../outside.txt", root) == [
            "file_exists: path '../outside.txt' escapes the root path"
        ]


class EvaluateFileNotExistsTests:
    def test_absent_path_passes(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_not_exists: an absent path yields no failures."""
        assert evaluate_file_not_exists("gone.txt", tmp_path) == []

    def test_present_path_fails(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_not_exists: an existing path fails with 'exists but must not'."""
        (tmp_path / "here.txt").write_text("x", encoding="utf-8")

        assert evaluate_file_not_exists("here.txt", tmp_path) == [
            "file_not_exists: path 'here.txt' exists but must not"
        ]

    def test_path_escaping_the_root_fails_closed(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_not_exists: a traversal outside the root reports an escape failure."""
        root = tmp_path / "sandbox"
        root.mkdir()

        assert evaluate_file_not_exists("../x", root) == ["file_not_exists: path '../x' escapes the root path"]


class EvaluateFileNotEmptyTests:
    def test_non_empty_file_passes(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_not_empty: a file with content yields no failures."""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")

        assert evaluate_file_not_empty("a.txt", tmp_path) == []

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            pytest.param("missing", "file_not_empty: path 'p' does not exist", id="missing"),
            pytest.param("dir", "file_not_empty: path 'p' is a directory, not a file", id="directory"),
            pytest.param("empty", "file_not_empty: path 'p' is empty (0 bytes)", id="empty"),
        ],
    )
    def test_unusable_path_fails_with_its_reason(self, tmp_path: Path, kind: str, expected: str) -> None:
        """[tier-1/unit] evaluate_file_not_empty: a missing path, a directory and a zero-byte file each fail with their own reason."""
        if kind == "dir":
            (tmp_path / "p").mkdir()
        elif kind == "empty":
            (tmp_path / "p").write_text("", encoding="utf-8")

        assert evaluate_file_not_empty("p", tmp_path) == [expected]

    def test_path_escaping_the_root_fails_closed(self, tmp_path: Path) -> None:
        """[tier-1/unit] evaluate_file_not_empty: a traversal outside the root reports an escape failure."""
        root = tmp_path / "sandbox"
        root.mkdir()

        assert evaluate_file_not_empty("../x", root) == ["file_not_empty: path '../x' escapes the root path"]
