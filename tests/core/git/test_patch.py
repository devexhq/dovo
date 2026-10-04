"""Tests for dovo.core.git.GitDiffParser."""

from __future__ import annotations

import pytest

from dovo.core.git import GitDiffParser


class GitDiffParserTests:
    """Unit tests for GitDiffParser.parse header/path/binary extraction."""

    @pytest.mark.parametrize(
        ("diff", "expected_paths", "expected_binary_paths", "expected_error"),
        [
            pytest.param(
                "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n",
                ["foo.py"],
                [],
                None,
                id="standard_diff_git_header",
            ),
            pytest.param(
                "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\ndiff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n",
                ["a.py", "b.py"],
                [],
                None,
                id="multiple_files_are_sorted",
            ),
            pytest.param(
                "diff --git foo.py foo.py\n--- foo.py\n+++ foo.py\n",
                ["foo.py"],
                [],
                None,
                id="loose_diff_git_header_without_ab_prefixes",
            ),
            pytest.param(
                "diff --git foo.py\n",
                [],
                [],
                "malformed diff --git header",
                id="malformed_loose_diff_git_header_is_reported",
            ),
            pytest.param(
                "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n",
                ["new.py", "old.py"],
                [],
                None,
                id="rename_from_and_to",
            ),
            pytest.param(
                "diff --git a/old.py b/new.py\ncopy from old.py\ncopy to new.py\n",
                ["new.py", "old.py"],
                [],
                None,
                id="copy_from_and_to",
            ),
            pytest.param(
                "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n",
                ["img.png"],
                ["img.png"],
                None,
                id="binary_files_header_marks_path_as_binary",
            ),
            pytest.param(
                "diff --git a/img.png b/img.png\nindex 111..222 100644\nGIT binary patch\nliteral 10\n",
                ["img.png"],
                ["img.png"],
                None,
                id="git_binary_patch_marks_current_section_as_binary",
            ),
            pytest.param(
                "--- /dev/null\n+++ /dev/null\nGIT binary patch\nliteral 10\n",
                [],
                ["(unknown)"],
                None,
                id="literal_or_delta_line_without_known_paths_marks_unknown",
            ),
            pytest.param(
                "diff --git a/foo.py b/foo.py\r\n--- a/foo.py\r\n+++ b/foo.py\r\n",
                ["foo.py"],
                [],
                None,
                id="crlf_line_endings_are_normalized",
            ),
            pytest.param(
                "not a diff at all\njust some text\n",
                [],
                [],
                "no file headers found (expected diff --git or --- / +++ )",
                id="no_file_headers_is_reported",
            ),
            pytest.param(
                "--- /dev/null\n+++ /dev/null\n",
                [],
                [],
                "no target file paths found in diff headers",
                id="headers_without_target_paths_is_reported",
            ),
        ],
    )
    def test_parse_diff_extracts_paths_and_errors(
        self,
        diff: str,
        expected_paths: list[str],
        expected_binary_paths: list[str],
        expected_error: str | None,
    ) -> None:
        paths, binary_paths, error = GitDiffParser(diff).parse()

        assert paths == expected_paths
        assert binary_paths == expected_binary_paths
        assert error == expected_error
