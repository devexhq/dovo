"""Tests for dovo.core.git.GitDiffParser and validate_patch_text."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.git import GitDiffParser, GitError, validate_patch_text
from tests.harness import new_file_diff


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


_BINARY_DIFF = "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"


class _Limits(NamedTuple):
    """The bounds one rejection case passes to validate_patch_text; None leaves a bound unset."""

    max_files: int | None = None
    max_patch_kb: int | None = None
    reject_binary_changes: bool | None = None


class ValidatePatchTextTests:
    """Contract tests for validate_patch_text's returned files and raised GitError."""

    def test_valid_patch_returns_sorted_touched_files(self, tmp_path: Path) -> None:
        """Return ["a.py", "b.py"] for a two-file diff listing b.py first, with no limits set."""
        touched = validate_patch_text(
            new_file_diff("b.py") + new_file_diff("a.py"),
            max_files=None,
            max_patch_kb=None,
            reject_binary_changes=None,
            worktree_path=tmp_path,
        )

        assert touched == ["a.py", "b.py"]

    @pytest.mark.parametrize(
        ("diff", "limits", "worktree", "error_code", "message", "remediations"),
        [
            pytest.param(
                "  \n",
                _Limits(),
                None,
                ErrorCode.PATCH_EMPTY,
                "Patch is empty or whitespace-only.",
                (Remediation(message="Ensure the agent returned a non-empty unified diff"),),
                id="empty",
            ),
            pytest.param(
                new_file_diff("a.txt", "x" * 2048),
                _Limits(max_patch_kb=1),
                None,
                ErrorCode.PATCH_SIZE_EXCEEDED,
                "Patch exceeds max_patch_kb (1 KiB) (size=2161 bytes).",
                (),
                id="too-large",
            ),
            pytest.param(
                new_file_diff("a.txt") + new_file_diff("b.txt"),
                _Limits(max_files=1),
                None,
                ErrorCode.PATCH_FILE_COUNT_EXCEEDED,
                "Patch touches 2 files; max_files is 1.",
                (Remediation(message="Set 'max_files' on the agent step."),),
                id="too-many-files",
            ),
            pytest.param(
                _BINARY_DIFF,
                _Limits(reject_binary_changes=True),
                None,
                ErrorCode.PATCH_BINARY_DISALLOWED,
                "Patch includes binary file changes which are rejected: logo.png.",
                (
                    Remediation(
                        message=(
                            "Avoid binary edits in the agent patch, or set 'reject_binary_changes=false' when allowed"
                        )
                    ),
                ),
                id="binary",
            ),
            pytest.param(
                "diff --git foo.py\n",
                _Limits(),
                None,
                ErrorCode.PATCH_CORRUPT,
                "Patch is not a valid unified diff: malformed diff --git header",
                (Remediation(message="Return a standard unified diff ('diff --git' / '--- +++' / '@@' hunks)"),),
                id="corrupt",
            ),
            pytest.param(
                new_file_diff("../escape.txt"),
                _Limits(),
                None,
                ErrorCode.PATCH_UNSAFE_PATH,
                "Patch path is absolute or escapes the worktree: '../escape.txt'.",
                (Remediation(message="Use worktree-relative paths only (no absolute paths or '..' segments)"),),
                id="unsafe",
            ),
            pytest.param(
                new_file_diff("a.txt"),
                _Limits(),
                "absent",
                ErrorCode.PATCH_WORKTREE_MISSING,
                "Worktree path does not exist or is not a directory: '{worktree}'.",
                (Remediation(message="Create the worktree before applying a patch"),),
                id="worktree-missing",
            ),
        ],
    )
    def test_rejection_raises_git_error_with_code_message_and_remediations(
        self,
        tmp_path: Path,
        diff: str,
        limits: _Limits,
        worktree: str | None,
        error_code: ErrorCode,
        message: str,
        remediations: tuple[Remediation, ...],
    ) -> None:
        """Raise GitError whose error_code, message and remediations equal the literal triple for each rejection."""
        worktree_path = tmp_path / worktree if worktree is not None else tmp_path

        with pytest.raises(GitError) as raised:
            validate_patch_text(
                diff,
                max_files=limits.max_files,
                max_patch_kb=limits.max_patch_kb,
                reject_binary_changes=limits.reject_binary_changes,
                worktree_path=worktree_path,
            )

        assert raised.value.error_code == error_code
        assert raised.value.message == message.format(worktree=worktree_path)
        assert raised.value.remediations == remediations
