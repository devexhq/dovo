"""Stateless Git command execution runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from dovo.common.constants import GIT_SUBPROCESS_TIMEOUT_SECONDS
from dovo.core.git.exceptions import (
    GitCommandError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
)
from dovo.core.git.models import GitWorktreeEntry


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


class GitRunner:
    """Stateless utility class containing static methods for executing Git commands."""

    @staticmethod
    def run(
        args: list[str],
        path: Path,
        *,
        input_text: str | None = None,
        timeout: int = GIT_SUBPROCESS_TIMEOUT_SECONDS,
        check: bool = True,
    ) -> str:
        """Execute a git command in path and return stripped stdout.

        Args:
            args: Command arguments after ``git``.
            path: Working directory for git command execution.
            input_text: Optional stdin input string.
            timeout: Subprocess timeout in seconds.
            check: When True, raises GitCommandError if returncode != 0.

        Returns:
            Stripped stdout text.

        Raises:
            GitNotFoundError: When git binary is not found.
            GitPlumbingTimeoutError: When subprocess times out.
            GitCommandError: When git command exits non-zero and check is True.
        """
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(path),
                input=input_text,
                capture_output=True,
                text=True,
                check=check,
                timeout=timeout,
            )
            return result.stdout.rstrip("\r\n")
        except FileNotFoundError as exc:
            raise GitNotFoundError(f"Git execution failed ('git {' '.join(args)}'): git not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitPlumbingTimeoutError(
                f"Git timed out after {timeout}s ('git {' '.join(args)}') (GIT_TIMEOUT)"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise GitCommandError(
                cmd=["git", *args],
                returncode=exc.returncode,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
            ) from exc

    @staticmethod
    def get_current_branch(path: Path) -> str:
        """Extract current active Git branch, returning 'HEAD (detached)' or 'unknown' on error."""
        try:
            branch = GitRunner.run(["branch", "--show-current"], path=path).strip()
            return branch if branch else "HEAD (detached)"
        except (GitNotFoundError, GitPlumbingTimeoutError, GitCommandError):
            return "unknown"

    @staticmethod
    def status_porcelain(path: Path) -> list[str]:
        """Return raw status porcelain lines for tracked/untracked working tree modifications."""
        try:
            output = GitRunner.run(["status", "--porcelain", "-u"], path=path)
            return [line for line in output.splitlines() if line.strip()]
        except (GitNotFoundError, GitCommandError):
            return []

    @staticmethod
    def has_uncommitted_changes(path: Path) -> bool:
        """Check whether working tree has modified, staged, untracked, or deleted files.

        Args:
            path: Path to git working directory.

        Returns:
            True if uncommitted changes exist, False otherwise.
        """
        return bool(GitRunner.status_porcelain(path))

    @staticmethod
    def worktree_add(
        path: Path,
        target_path: Path,
        branch: str,
        base_ref: str,
    ) -> None:
        """Add a new git worktree with a new branch."""
        GitRunner.run(
            ["worktree", "add", "-b", branch, str(target_path), base_ref],
            path=path,
        )

    @staticmethod
    def worktree_remove(
        path: Path,
        target_path: Path,
        *,
        force: bool = True,
    ) -> None:
        """Remove a git worktree."""
        cmd = ["worktree", "remove", str(target_path)]
        if force:
            cmd.append("--force")
        GitRunner.run(cmd, path=path)

    @staticmethod
    def worktree_prune(path: Path) -> None:
        """Prune stale git worktree administrative records."""
        GitRunner.run(["worktree", "prune"], path=path)

    @staticmethod
    def worktree_list(path: Path) -> list[GitWorktreeEntry]:
        """Parse and return all registered worktrees via `git worktree list --porcelain`."""
        output = GitRunner.run(["worktree", "list", "--porcelain"], path=path)
        return parse_worktree_porcelain(output)

    @staticmethod
    def branch_delete(
        path: Path,
        branch: str,
        *,
        force: bool = True,
    ) -> None:
        """Delete a git branch."""
        flag = "-D" if force else "-d"
        GitRunner.run(["branch", flag, branch], path=path)

    @staticmethod
    def list_branches(path: Path, pattern: str | None = None) -> list[str]:
        """List local branch names matching optional pattern."""
        ref_pattern = f"refs/heads/{pattern}" if pattern else "refs/heads/"
        output = GitRunner.run(
            ["for-each-ref", "--format=%(refname:short)", ref_pattern],
            path=path,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    @staticmethod
    def rev_parse(path: Path, rev: str = "HEAD") -> str:
        """Resolve a git revision into a commit SHA."""
        return GitRunner.run(["rev-parse", rev], path=path)

    @staticmethod
    def add_intent_to_add(path: Path, target: str = ".") -> None:
        """Add untracked files to index with intent-to-add flag."""
        try:
            GitRunner.run(["add", "-N", target], path=path)
        except (GitNotFoundError, GitCommandError):
            pass

    @staticmethod
    def diff(
        path: Path,
        base_commit: str,
        *,
        binary: bool = True,
    ) -> str:
        """Generate diff against base_commit."""
        cmd = ["diff"]
        if binary:
            cmd.append("--binary")
        cmd.append(base_commit)
        return GitRunner.run(cmd, path=path)

    @staticmethod
    def diff_name_only(path: Path, base_commit: str) -> list[str]:
        """Return list of modified file paths relative to repository root."""
        output = GitRunner.run(["diff", "--name-only", base_commit], path=path)
        return [line.strip() for line in output.splitlines() if line.strip()]

    @staticmethod
    def diff_stat(path: Path, base_commit: str) -> str:
        """Return diffstat summary against base_commit."""
        return GitRunner.run(["diff", "--stat", base_commit], path=path)

    @staticmethod
    def apply_check(
        path: Path,
        diff_text: str,
        *,
        binary: bool = True,
    ) -> tuple[int, str, str]:
        """Dry-run check whether a patch applies cleanly.

        Returns:
            Tuple of (returncode, stdout, stderr).
        """
        cmd = ["git", "apply", "--check"]
        if binary:
            cmd.append("--binary")
        cmd.append("-")

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(path),
                input=diff_text + "\n",
                capture_output=True,
                text=True,
                timeout=GIT_SUBPROCESS_TIMEOUT_SECONDS,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            raise GitPlumbingTimeoutError(
                f"Git timed out after {GIT_SUBPROCESS_TIMEOUT_SECONDS}s ('git apply --check') (GIT_TIMEOUT)"
            ) from exc
        except FileNotFoundError as exc:
            raise GitNotFoundError("Git execution failed: git not found") from exc

    @staticmethod
    def apply(
        path: Path,
        diff_text: str,
        *,
        binary: bool = True,
    ) -> tuple[int, str, str]:
        """Apply a patch to the working tree.

        Returns:
            Tuple of (returncode, stdout, stderr).
        """
        cmd = ["git", "apply"]
        if binary:
            cmd.append("--binary")
        cmd.append("-")

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(path),
                input=diff_text + "\n",
                capture_output=True,
                text=True,
                timeout=GIT_SUBPROCESS_TIMEOUT_SECONDS,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            raise GitPlumbingTimeoutError(
                f"Git timed out after {GIT_SUBPROCESS_TIMEOUT_SECONDS}s ('git apply') (GIT_TIMEOUT)"
            ) from exc
        except FileNotFoundError as exc:
            raise GitNotFoundError("Git execution failed: git not found") from exc

    @staticmethod
    def add_all(path: Path) -> None:
        """Stage all modifications and untracked files."""
        GitRunner.run(["add", "-A"], path=path)

    @staticmethod
    def commit(path: Path, message: str) -> None:
        """Create a commit with the specified commit message."""
        GitRunner.run(["commit", "-m", message], path=path)
