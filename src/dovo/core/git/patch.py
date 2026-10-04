"""Validate unified diffs against size/count/binary/path limits (no git apply)."""

from __future__ import annotations

import re
from pathlib import Path

from dovo.core.git.exceptions import MalformedDiffHeader
from dovo.core.git.models import PatchApplyResult, PatchApplyStatus

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_MINUS_RE = re.compile(r"^--- (?:a/)?(.+)$")
_PLUS_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_COPY_FROM_RE = re.compile(r"^copy from (.+)$")
_COPY_TO_RE = re.compile(r"^copy to (.+)$")
_BINARY_FILES_RE = re.compile(r"^Binary files (?:a/)?(.+) and (?:b/)?(.+) differ\s*$")
_GIT_BINARY_PATCH_RE = re.compile(r"^GIT binary patch\s*$")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")


class GitDiffParser:
    """Parse a unified diff for target paths and binary markers."""

    # Tried in order for each line; first handler to return truthy wins.
    _HANDLERS = (
        "_parse_diff_git_header",
        "_parse_loose_diff_git_header",
        "_parse_old_file_header",
        "_parse_new_file_header",
        "_parse_rename_or_copy_from",
        "_parse_rename_or_copy_to",
        "_parse_binary_files_header",
        "_mark_git_binary_patch_paths",
        "_mark_literal_or_delta_binary_paths",
    )

    def __init__(self, unified_diff: str):
        self.has_file_header = False
        self.paths: set[str] = set()
        self.binary_paths: set[str] = set()
        # Paths belonging to the current ``diff --git`` file section.
        self.section_paths: set[str] = set()
        self.unified_diff = unified_diff
        self.line = ""

    def _record_path(
        self, raw: str, *, paths: bool = False, section_paths: bool = False, binary_paths: bool = False
    ) -> None:
        """Normalize and record a path token into context collections."""
        if paths:
            _add_path(self.paths, raw)

        if section_paths:
            _add_path(self.section_paths, raw)

        if binary_paths:
            _add_path(self.binary_paths, raw)

    def _record_binary_fallback(self) -> None:
        """Mark the current section (or all known paths) as binary."""
        if self.section_paths:
            self.binary_paths.update(self.section_paths)
        elif self.paths:
            self.binary_paths.update(self.paths)
        else:
            self.binary_paths.add("(unknown)")

    def _parse_diff_git_header(self):
        """Parse standard diff --git header if line matches."""
        m = _DIFF_GIT_RE.match(self.line)
        if m:
            self.has_file_header = True
            self.section_paths = set()

            for group in m.groups():
                self._record_path(group, paths=True, section_paths=True)

            return True

    def _parse_loose_diff_git_header(self):
        """Parse loose diff --git header with relaxed token boundaries."""
        if not self.line.startswith("diff --git "):
            return False

        rest = self.line[len("diff --git ") :].strip()
        parts = rest.split(" ", 1)

        if len(parts) != 2:
            raise MalformedDiffHeader("malformed diff --git header")

        self.has_file_header = True
        self.section_paths = set()

        for part in parts:
            token = _strip_ab_prefix(part)
            self._record_path(token, paths=True, section_paths=True)

        return True

    def _parse_old_file_header(self):
        """Parse minus (---) old file diff header."""
        if self.line.startswith("--- "):
            self.has_file_header = True
            body = self.line[4:]

            if body != "/dev/null":
                m = _MINUS_RE.match(self.line)
                raw = m.group(1) if m else body
                self._record_path(raw, paths=True, section_paths=True)

            return True

    def _parse_new_file_header(self):
        """Parse plus (+++) new file diff header."""
        if self.line.startswith("+++ "):
            self.has_file_header = True
            body = self.line[4:]
            if body != "/dev/null":
                m = _PLUS_RE.match(self.line)
                raw = m.group(1) if m else body
                self._record_path(raw, paths=True, section_paths=True)
            return True

    def _parse_rename_or_copy_from(self):
        """Parse rename from or copy from diff header."""
        m = _RENAME_FROM_RE.match(self.line) or _COPY_FROM_RE.match(self.line)
        if m:
            self._record_path(m.group(1), paths=True, section_paths=True)
            return True

    def _parse_rename_or_copy_to(self):
        """Parse rename to or copy to diff header."""
        m = _RENAME_TO_RE.match(self.line) or _COPY_TO_RE.match(self.line)
        if m:
            self._record_path(m.group(1), paths=True, section_paths=True)
            return True

    def _parse_binary_files_header(self):
        """Parse Binary files diff header."""
        m = _BINARY_FILES_RE.match(self.line)
        if m:
            self.has_file_header = True
            for group in m.groups():
                self._record_path(group, paths=True, section_paths=True, binary_paths=True)
            return True

    def _mark_git_binary_patch_paths(self):
        """Mark binary patch paths if GIT binary patch header is encountered."""
        if _GIT_BINARY_PATCH_RE.match(self.line):
            self._record_binary_fallback()
            return True

    def _mark_literal_or_delta_binary_paths(self):
        """Mark binary patch paths if literal or delta binary chunk starts."""
        if self.line.startswith("literal ") or self.line.startswith("delta "):
            self._record_binary_fallback()
            return True

    def _parse_line(self) -> None:
        """Try each header handler in turn, stopping at the first match."""
        for name in self._HANDLERS:
            if getattr(self, name)():
                return

    def parse(self):
        """Run the parse operation."""
        text = self.unified_diff.replace("\r\n", "\n").replace("\r", "\n")

        try:
            for line in text.split("\n"):
                self.line = line
                self._parse_line()
        except MalformedDiffHeader as exc:
            return [], [], str(exc)

        if not self.has_file_header:
            return (
                [],
                [],
                "no file headers found (expected diff --git or --- / +++ )",
            )

        if not self.paths and not self.binary_paths:
            return [], [], "no target file paths found in diff headers"

        return sorted(self.paths), sorted(self.binary_paths), None


def _normalize_diff_path(raw: str) -> str | None:
    """Normalize a path token from a diff header to a relative posix path.

    Returns:
        Normalized path, or ``None`` when the token is a null device marker.
    """
    path = raw.strip()
    if not path or path == "/dev/null":
        return None
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path.replace("\\", "/")


def _add_path(paths: set[str], raw: str) -> None:
    """Normalize raw path string and add it to target path set."""
    norm = _normalize_diff_path(raw)
    if norm is not None:
        paths.add(norm)


def _strip_ab_prefix(token: str) -> str:
    """Strip leading a/ or b/ Git diff prefix from token."""
    if token.startswith("a/") or token.startswith("b/"):
        return token[2:]
    return token


def _is_unsafe_path(rel_path: str, worktree_path: Path) -> bool:
    """Return True if ``rel_path`` is absolute or would escape ``worktree_path``."""
    if not rel_path or rel_path == "(unknown)":
        return True
    candidate = Path(rel_path)
    if candidate.is_absolute():
        return True
    if _DRIVE_PATH_RE.match(rel_path):
        return True
    if any(part == ".." for part in candidate.parts):
        return True
    try:
        worktree_resolved = worktree_path.resolve()
        resolved = (worktree_resolved / candidate).resolve()
        resolved.relative_to(worktree_resolved)
    except (OSError, ValueError):
        return True
    return False


def _empty_or_large_patch_result(
    unified_diff: object,
    *,
    max_patch_kb: int,
) -> PatchApplyResult | None:
    """Return an early failure for empty/oversized patches, else None."""
    if not isinstance(unified_diff, str) or not unified_diff.strip():
        return PatchApplyResult(
            status=PatchApplyStatus.EMPTY_DIFF,
            errors=["Patch is empty or whitespace-only."],
            fixes=["Ensure the agent returned a non-empty unified diff"],
        )
    size_bytes = len(unified_diff.encode("utf-8"))
    max_bytes = max_patch_kb * 1024
    if size_bytes > max_bytes:
        return PatchApplyResult(
            status=PatchApplyStatus.TOO_LARGE,
            errors=[f"Patch exceeds max_patch_kb ({max_patch_kb} KiB) (size={size_bytes} bytes)."],
            fixes=["Reduce agent change size or raise patch.max_patch_kb / workflow patch.max_patch_kb"],
        )
    return None


def _patch_parse_and_limits_result(
    unified_diff: str,
    *,
    max_files: int,
    reject_binary_changes: bool,
) -> tuple[list[str], PatchApplyResult | None]:
    """Parse diff headers and enforce file-count / binary gates."""
    git_diff_parser = GitDiffParser(unified_diff)
    touched, binary_paths, parse_error = git_diff_parser.parse()
    if parse_error is not None:
        return [], PatchApplyResult(
            status=PatchApplyStatus.INVALID_DIFF,
            errors=[f"Patch is not a valid unified diff: {parse_error}"],
            fixes=["Return a standard unified diff (diff --git / --- +++ / @@ hunks)"],
        )
    if len(touched) > max_files:
        return touched, PatchApplyResult(
            status=PatchApplyStatus.TOO_MANY_FILES,
            errors=[f"Patch touches {len(touched)} files; max_files is {max_files}."],
            fixes=["Split the change or raise patch.max_files"],
        )
    if reject_binary_changes and binary_paths:
        joined = ", ".join(binary_paths) if binary_paths else "(binary change)"
        return touched, PatchApplyResult(
            status=PatchApplyStatus.BINARY_REJECTED,
            touched_files=list(touched),
            errors=[f"Patch includes binary file changes which are rejected: {joined}."],
            fixes=["Avoid binary edits in the agent patch, or set reject_binary_changes=false when allowed"],
        )
    return touched, None


def _worktree_path_safety_result(
    touched: list[str],
    worktree_path: Path,
) -> PatchApplyResult | None:
    """Reject missing worktrees and paths that escape the worktree root."""
    try:
        worktree_ok = worktree_path.is_dir()
    except OSError:
        worktree_ok = False
    if not worktree_ok:
        return PatchApplyResult(
            status=PatchApplyStatus.WORKTREE_MISSING,
            touched_files=list(touched),
            errors=[f"Worktree path does not exist or is not a directory: '{worktree_path}'."],
            fixes=["Create the worktree before applying a patch"],
        )
    for rel in touched:
        if _is_unsafe_path(rel, worktree_path):
            return PatchApplyResult(
                status=PatchApplyStatus.UNSAFE_PATH,
                touched_files=list(touched),
                errors=[f"Patch path is absolute or escapes the worktree: '{rel}'."],
                fixes=["Use worktree-relative paths only (no absolute paths or '..' segments)"],
            )
    return None


def validate_patch_text(
    unified_diff: str,
    *,
    max_files: int,
    max_patch_kb: int,
    reject_binary_changes: bool,
    worktree_path: Path,
) -> PatchApplyResult:
    """Validate diff text against size/count/binary/path limits, no git apply.

    Returns ``PatchApplyResult(status=CHECKED_OK, touched_files=...)`` when all
    checks pass; otherwise a result carrying the failing status. Used as the
    post-hoc gate for direct-mutation providers, whose changes are already
    reflected on disk.

    Args:
        unified_diff: Full unified diff text.
        max_files: Maximum distinct target files allowed.
        max_patch_kb: Maximum UTF-8 byte size of the diff in KiB.
        reject_binary_changes: When True, reject binary file markers.
        worktree_path: Worktree root used for the unsafe-path check.

    Returns:
        Structured :class:`PatchApplyResult` with status, touched files, errors.
    """
    early = _empty_or_large_patch_result(unified_diff, max_patch_kb=max_patch_kb)
    if early is not None:
        return early

    touched, limit_result = _patch_parse_and_limits_result(
        unified_diff,
        max_files=max_files,
        reject_binary_changes=reject_binary_changes,
    )
    if limit_result is not None:
        return limit_result

    safety = _worktree_path_safety_result(touched, worktree_path)
    if safety is not None:
        return safety

    return PatchApplyResult(
        status=PatchApplyStatus.CHECKED_OK,
        touched_files=list(touched),
    )
