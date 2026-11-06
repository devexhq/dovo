"""Stateless Git command execution runner."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from dovo.common.constants import GIT_SUBPROCESS_TIMEOUT_SECONDS
from dovo.common.error_codes import ErrorCode
from dovo.core.git.exceptions import GitError, git_command_error
from dovo.core.git.models import GitBytesOutput, GitProcessOutput, GitWorktreeEntry

_BRANCH_UNKNOWN_CODES: Final = frozenset({ErrorCode.GIT_BINARY_MISSING, ErrorCode.GIT_FAILED})
_GIT_TIMEOUT_ENV: Final = "DOVO_GIT_TIMEOUT_SECONDS"
_STATUS_EMPTY_CODES: Final = frozenset({ErrorCode.GIT_BINARY_MISSING, ErrorCode.GIT_FAILED})
_CONFLICT_PATTERNS: Final = (
    re.compile(r"^error:\s+patch failed:\s+([^:]+)"),
    re.compile(r"^error:\s+([^:]+):\s+patch does not apply"),
    re.compile(r"cannot apply binary patch to '([^']+)'"),
    re.compile(r"^error:\s+([^:]+):\s+(?:already exists in working directory|does not exist in index)"),
    re.compile(r"^error:\s+([^:]+):\s+No such file or directory"),
)


def _extract_branch_name(raw_branch: str) -> str:
    """Strip refs/heads/ prefix from git branch name."""
    return raw_branch.removeprefix("refs/heads/").strip()


def _parse_flag_attribute(entry_data: dict[str, Any], line: str) -> bool:
    """Parse boolean and status flags from porcelain line."""
    if line == "bare":
        entry_data["is_bare"] = True
        return True
    if line == "detached":
        entry_data["is_detached"] = True
        return True
    if line.startswith("locked"):
        entry_data["is_locked"] = True
        return True
    if line.startswith("prunable"):
        entry_data["is_prunable"] = True
        reason = line.removeprefix("prunable").strip()
        entry_data["prunable_reason"] = reason if reason else None
        return True
    return False


def _parse_worktree_attribute(entry_data: dict[str, Any], line: str) -> None:
    """Extract a single porcelain attribute line into entry dictionary."""
    if _parse_flag_attribute(entry_data, line):
        return
    if line.startswith("worktree "):
        entry_data["path"] = Path(line.removeprefix("worktree ").strip())
    elif line.startswith("HEAD "):
        entry_data["head_sha"] = line.removeprefix("HEAD ").strip()
    elif line.startswith("branch "):
        entry_data["branch"] = _extract_branch_name(line.removeprefix("branch "))


def _parse_worktree_stanza(lines: list[str]) -> GitWorktreeEntry | None:
    """Convert accumulated lines of one worktree block into GitWorktreeEntry."""
    data: dict[str, Any] = {
        "head_sha": "",
        "branch": None,
        "is_bare": False,
        "is_detached": False,
        "is_locked": False,
        "is_prunable": False,
        "prunable_reason": None,
    }
    for line in lines:
        _parse_worktree_attribute(data, line)
    if "path" not in data or data["path"] is None:
        return None
    return GitWorktreeEntry(**data)


def _collect_stanzas(output: str) -> list[list[str]]:
    """Split porcelain text into lists of lines per stanza."""
    stanzas: list[list[str]] = []
    current: list[str] = []
    for line in output.splitlines():
        trimmed = line.strip()
        if not trimmed:
            if current:
                stanzas.append(current)
                current = []
        else:
            current.append(trimmed)
    if current:
        stanzas.append(current)
    return stanzas


def parse_worktree_porcelain(output: str) -> list[GitWorktreeEntry]:
    """Parse output from `git worktree list --porcelain` into GitWorktreeEntry list.

    Args:
        output: Raw stdout from `git worktree list --porcelain`.

    Returns:
        List of parsed GitWorktreeEntry records.
    """
    entries: list[GitWorktreeEntry] = []
    for stanza in _collect_stanzas(output):
        entry = _parse_worktree_stanza(stanza)
        if entry is not None:
            entries.append(entry)
    return entries


def _match_conflict_line(line: str) -> str | None:
    """Return conflicting file path from a single line of git stderr, or None."""
    trimmed = line.strip()
    for pattern in _CONFLICT_PATTERNS:
        match = pattern.search(trimmed)
        if match:
            return match.group(1).strip()
    return None


def extract_conflicts(stderr: str) -> list[str]:
    """Extract conflicting file paths from git apply stderr output.

    Args:
        stderr: Standard error output from git apply or git apply --check.

    Returns:
        Sorted list of unique conflicting file paths.
    """
    conflicts: set[str] = set()
    for line in stderr.splitlines():
        path = _match_conflict_line(line)
        if path:
            conflicts.add(path)
    return sorted(conflicts)


def _git_timeout_seconds() -> float:
    """Return DOVO_GIT_TIMEOUT_SECONDS read now when it is a positive number, else GIT_SUBPROCESS_TIMEOUT_SECONDS."""
    raw_override = os.environ.get(_GIT_TIMEOUT_ENV)
    if raw_override is None:
        return GIT_SUBPROCESS_TIMEOUT_SECONDS
    try:
        override = float(raw_override)
    except ValueError:
        return GIT_SUBPROCESS_TIMEOUT_SECONDS
    return override if override > 0 else GIT_SUBPROCESS_TIMEOUT_SECONDS


@contextmanager
def _spawn_failures(args: Sequence[str], path: Path, timeout: float) -> Generator[None]:
    """Map the subprocess.run failures of `git <args>` in path, bounded by timeout seconds, to GitError.

    Raises:
        GitError: With GIT_WORKDIR_MISSING when path is not a directory, chained to the OSError.
        GitError: With GIT_BINARY_MISSING when git binary is not found, or GIT_TIMEOUT when subprocess.run
            times out.
    """
    try:
        yield
    except FileNotFoundError as exc:
        if not path.is_dir():
            # Chained: a vanished working directory is a broken workspace, so catch_failure records it.
            raise GitError(
                ErrorCode.GIT_WORKDIR_MISSING,
                f"Git execution failed ('git {' '.join(args)}'): working directory '{path}' does not exist",
            ) from exc
        # Not chained: a missing git binary is fully named by its code, so catch_failure leaves no event file.
        raise GitError(
            ErrorCode.GIT_BINARY_MISSING, f"Git execution failed ('git {' '.join(args)}'): git not found"
        ) from None
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            ErrorCode.GIT_TIMEOUT,
            f"Git timed out after {timeout:g}s ('git {' '.join(args)}')",
        ) from exc


def _merged_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    """Return env merged over the process environment, or None to inherit it unchanged."""
    return {**os.environ, **env} if env else None


class GitRunner:
    """Executes git commands. Built once per process by CliContext.build, and once per agent invocation by the direct-mutation adapter."""

    def exec(
        self,
        args: Sequence[str],
        path: Path,
        *,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> GitProcessOutput:
        """Start `git <args>` in path, bounded by DOVO_GIT_TIMEOUT_SECONDS or else GIT_SUBPROCESS_TIMEOUT_SECONDS, and return its result for any exit code.

        Args:
            args: Command arguments after ``git``.
            path: Working directory for git command execution.
            input_text: Optional stdin input string.
            env: Extra environment variables merged over the process environment.

        Returns:
            The exit code, stdout and stderr of the git process.

        Raises:
            GitError: With GIT_WORKDIR_MISSING when path is not a directory, chained to the OSError.
            GitError: With GIT_BINARY_MISSING when git binary is not found, or GIT_TIMEOUT when subprocess.run
                times out.
        """
        timeout = _git_timeout_seconds()
        with _spawn_failures(args, path, timeout):
            proc = subprocess.run(
                ["git", *args],
                cwd=str(path),
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=_merged_env(env),
            )
        return GitProcessOutput(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def exec_bytes(
        self,
        args: Sequence[str],
        path: Path,
        *,
        input_bytes: bytes | None = None,
        env: Mapping[str, str] | None = None,
    ) -> GitBytesOutput:
        """Start `git <args>` in path like exec, feeding and capturing raw bytes, and return its result for any exit code.

        Args:
            args: Command arguments after ``git``.
            path: Working directory for git command execution.
            input_bytes: Optional stdin bytes.
            env: Extra environment variables merged over the process environment.

        Returns:
            The exit code and the undecoded stdout and stderr of the git process.

        Raises:
            GitError: With GIT_WORKDIR_MISSING, GIT_BINARY_MISSING or GIT_TIMEOUT, as exec raises them.
        """
        timeout = _git_timeout_seconds()
        with _spawn_failures(args, path, timeout):
            proc = subprocess.run(
                ["git", *args],
                cwd=str(path),
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=timeout,
                env=_merged_env(env),
            )
        return GitBytesOutput(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def run_bytes(
        self,
        args: Sequence[str],
        path: Path,
        *,
        input_bytes: bytes | None = None,
        env: Mapping[str, str] | None = None,
    ) -> bytes:
        """Execute `git <args>` through exec_bytes and return its stdout unchanged.

        Raises:
            GitError: With GIT_BINARY_MISSING, GIT_TIMEOUT or GIT_WORKDIR_MISSING from exec_bytes, or GIT_FAILED
                naming the stderr, decoded leniently, when git exits non-zero.
        """
        result = self.exec_bytes(args, path, input_bytes=input_bytes, env=env)
        if result.returncode != 0:
            raise git_command_error(
                args,
                result.returncode,
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"),
            )
        return result.stdout

    def run(
        self,
        args: list[str],
        path: Path,
        *,
        input_text: str | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Execute `git <args>` through exec and return stdout without trailing newlines.

        Args:
            args: Command arguments after ``git``.
            path: Working directory for git command execution.
            input_text: Optional stdin input string.
            check: When True, raises a GIT_FAILED GitError if returncode != 0.
            env: Extra environment variables merged over the process environment.

        Returns:
            Stripped stdout text.

        Raises:
            GitError: With GIT_BINARY_MISSING, GIT_TIMEOUT or GIT_WORKDIR_MISSING from exec, or GIT_FAILED when git
                exits non-zero and check is True.
        """
        result = self.exec(args, path, input_text=input_text, env=env)
        if check and result.returncode != 0:
            raise git_command_error(args, result.returncode, result.stdout, result.stderr)
        return result.stdout.rstrip("\r\n")

    def get_current_branch(self, path: Path) -> str:
        """Extract current active Git branch, returning 'HEAD (detached)', or 'unknown' when git is missing or fails.

        Raises:
            GitError: With GIT_TIMEOUT when git times out, or GIT_WORKDIR_MISSING when path is not a directory.
        """
        try:
            branch = self.run(["branch", "--show-current"], path=path).strip()
            return branch if branch else "HEAD (detached)"
        except GitError as exc:
            if exc.error_code not in _BRANCH_UNKNOWN_CODES:
                raise
            return "unknown"

    def status_porcelain(self, path: Path) -> list[str]:
        """Return raw status porcelain lines for tracked/untracked working tree modifications, or [] when git is missing or fails."""
        try:
            output = self.run(["status", "--porcelain", "-u"], path=path)
            return [line for line in output.splitlines() if line.strip()]
        except GitError as exc:
            if exc.error_code not in _STATUS_EMPTY_CODES:
                raise
            return []

    def has_uncommitted_changes(self, path: Path) -> bool:
        """Check whether working tree has modified, staged, untracked, or deleted files.

        Args:
            path: Path to git working directory.

        Returns:
            True if uncommitted changes exist, False otherwise.
        """
        return bool(self.status_porcelain(path))

    def dirty_paths(self, path: Path) -> list[str]:
        """Return sorted unquoted paths with staged, unstaged or untracked changes, listing both sides of a rename or copy.

        Raises:
            GitError: With GIT_BINARY_MISSING, GIT_TIMEOUT, or GIT_FAILED when git status exits non-zero.
        """
        fields = iter(self.run(["status", "--porcelain=v1", "-z", "-uall"], path=path).split("\0"))
        paths: set[str] = set()
        for entry in fields:
            if not entry:
                continue
            paths.add(entry[3:])
            if entry[0] in "RC" or entry[1] in "RC":
                paths.add(next(fields))
        return sorted(paths)

    def worktree_add(
        self,
        path: Path,
        target_path: Path,
        branch: str,
        base_ref: str,
    ) -> None:
        """Add a new git worktree with a new branch."""
        self.run(
            ["worktree", "add", "-b", branch, str(target_path), base_ref],
            path=path,
        )

    def worktree_remove(
        self,
        path: Path,
        target_path: Path,
        *,
        force: bool = True,
    ) -> None:
        """Remove a git worktree."""
        cmd = ["worktree", "remove", str(target_path)]
        if force:
            cmd.append("--force")
        self.run(cmd, path=path)

    def worktree_prune(self, path: Path) -> None:
        """Prune stale git worktree administrative records."""
        self.run(["worktree", "prune"], path=path)

    def worktree_list(self, path: Path) -> list[GitWorktreeEntry]:
        """Parse and return all registered worktrees via `git worktree list --porcelain`."""
        output = self.run(["worktree", "list", "--porcelain"], path=path)
        return parse_worktree_porcelain(output)

    def branch_delete(
        self,
        path: Path,
        branch: str,
        *,
        force: bool = True,
    ) -> None:
        """Delete a git branch."""
        flag = "-D" if force else "-d"
        self.run(["branch", flag, branch], path=path)

    def list_branches(self, path: Path, pattern: str | None = None) -> list[str]:
        """List local branch names matching optional pattern."""
        ref_pattern = f"refs/heads/{pattern}" if pattern else "refs/heads/"
        output = self.run(
            ["for-each-ref", "--format=%(refname:short)", ref_pattern],
            path=path,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def branch_exists(self, path: Path, branch: str) -> bool:
        """Return True when refs/heads/<branch> exists; raise for git failures other than a missing ref."""
        args = ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
        result = self.exec(args, path)
        if result.returncode == 1:
            return False
        if result.returncode != 0:
            raise git_command_error(args, result.returncode, result.stdout, result.stderr)
        return True

    def rev_parse(self, path: Path, rev: str = "HEAD") -> str:
        """Resolve a git revision into a commit SHA."""
        return self.run(["rev-parse", rev], path=path)

    def patch_paths(self, path: Path, diff_text: str) -> list[str]:
        """Return sorted unquoted paths diff_text touches, listing both sides of a rename.

        Reads the patch forwards for post-image names and in reverse for pre-image names,
        so the result describes exactly the patch that is applied.

        Raises:
            GitError: GIT_FAILED when git apply cannot parse diff_text.
        """
        paths: set[str] = set()
        for args in (["apply", "--numstat", "-z", "-"], ["apply", "-R", "--numstat", "-z", "-"]):
            output = self.run(args, path=path, input_text=diff_text + "\n")
            paths.update(entry.split("\t", 2)[2] for entry in output.split("\0") if entry)
        return sorted(paths)

    def apply_check(
        self,
        path: Path,
        diff_text: str,
        *,
        binary: bool = True,
    ) -> list[str]:
        """Dry-run check whether a patch applies cleanly and return the conflicting paths, empty when it does.

        Returns:
            Sorted conflicting paths the failed check names in its stderr, or [] when the check passes.

        Raises:
            GitError: GIT_FAILED with the check's stderr, else "patch check failed", when it fails naming no path.
        """
        args = ["apply", "--check"]
        if binary:
            args.append("--binary")
        args.append("-")
        result = self.exec(args, path, input_text=diff_text + "\n")
        if result.returncode == 0:
            return []
        conflicts = extract_conflicts(result.stderr)
        if not conflicts:
            raise GitError(ErrorCode.GIT_FAILED, result.stderr.strip() or "patch check failed")
        return conflicts

    def apply(
        self,
        path: Path,
        diff_text: str,
        *,
        binary: bool = True,
        index: bool = False,
    ) -> None:
        """Apply a patch to the working tree, and to the index too when index is True.

        Raises:
            GitError: GIT_FAILED with git apply's stderr, else "patch failed", when it exits non-zero.
        """
        args = ["apply"]
        if binary:
            args.append("--binary")
        if index:
            args.append("--index")
        args.append("-")
        result = self.exec(args, path, input_text=diff_text + "\n")
        if result.returncode != 0:
            raise GitError(ErrorCode.GIT_FAILED, result.stderr.strip() or "patch failed")

    def commit(self, path: Path, message: str, *, paths: list[str]) -> None:
        """Commit only the literal paths from the working tree, leaving every other index entry as it was.

        Raises:
            ValueError: When paths is empty, since an empty pathspec would commit the whole index.
            GitError: GIT_FAILED when git commit exits non-zero (for example a failing hook).
        """
        if not paths:
            raise ValueError("GitRunner.commit requires at least one path")
        self.run(
            [
                "--literal-pathspecs",
                "commit",
                "--only",
                "-m",
                message,
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
            ],
            path=path,
            input_text="\0".join(paths),
        )
