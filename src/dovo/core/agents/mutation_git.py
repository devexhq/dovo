"""Sandbox-only git helpers for direct-mutation agent providers.

Direct-mutation providers (e.g. Cursor) edit sandbox files on disk instead of
returning a diff. These helpers baseline the sandbox before the agent runs,
capture what changed, and discard agent edits back to that baseline. All
operations run with ``cwd`` set to the sandbox path only — never the user's
main worktree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from dovo.common.constants import GIT_SUBPROCESS_TIMEOUT_SECONDS


class MutationGitError(RuntimeError):
    """Raised when a sandbox git operation for a direct-mutation provider fails."""


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Run git command in subprocess under cwd with timeout handling."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            check=False,
            timeout=GIT_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MutationGitError(
            f"git {' '.join(args)} timed out after {GIT_SUBPROCESS_TIMEOUT_SECONDS}s (GIT_TIMEOUT)"
        ) from exc
    except OSError as exc:
        raise MutationGitError(f"failed to run git {' '.join(args)}: {exc}") from exc


def _require_ok(completed: subprocess.CompletedProcess[bytes], *, action: str) -> None:
    """Verify that subprocess exit code is zero or raise MutationGitError."""
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise MutationGitError(f"{action} failed: {detail or completed.returncode}")


def resolve_pre_agent_baseline(sandbox_path: Path) -> str:
    """Return a git ref that captures sandbox state before an agent runs.

    A clean tree baselines to ``HEAD``. A dirty tree (e.g. a ``--wip`` overlay
    applied uncommitted changes) is baselined by an internal marker commit, so
    a later reset to this ref never discards state that predates the agent.

    Args:
        sandbox_path: Sandbox checkout to baseline.

    Returns:
        A git commit SHA usable as a reset/diff target.

    Raises:
        MutationGitError: When the underlying git commands fail.
    """
    status = _run_git(["status", "--porcelain"], cwd=sandbox_path)
    _require_ok(status, action="git status")
    if not status.stdout.strip():
        head = _run_git(["rev-parse", "HEAD"], cwd=sandbox_path)
        _require_ok(head, action="git rev-parse HEAD")
        return head.stdout.decode("utf-8").strip()

    add = _run_git(["add", "-A"], cwd=sandbox_path)
    _require_ok(add, action="git add -A (pre-agent baseline)")
    commit = _run_git(
        ["commit", "--no-verify", "-m", "dovo: pre-agent baseline"],
        cwd=sandbox_path,
    )
    _require_ok(commit, action="git commit (pre-agent baseline)")
    head = _run_git(["rev-parse", "HEAD"], cwd=sandbox_path)
    _require_ok(head, action="git rev-parse HEAD")
    return head.stdout.decode("utf-8").strip()


def capture_diff_since(sandbox_path: Path, baseline: str) -> tuple[str, list[str]]:
    """Stage all sandbox changes and diff them against ``baseline``.

    Covers modified/new/deleted files and any commits the agent made between
    ``baseline`` and the current tree, since the index is compared directly
    against the baseline commit's tree.

    Args:
        sandbox_path: Sandbox checkout to inspect.
        baseline: Ref returned by :func:`resolve_pre_agent_baseline`.

    Returns:
        Tuple of ``(unified_diff, touched_files)``.

    Raises:
        MutationGitError: When the underlying git commands fail.
    """
    add = _run_git(["add", "-A"], cwd=sandbox_path)
    _require_ok(add, action="git add -A (capture diff)")
    diff = _run_git(["diff", "--cached", baseline], cwd=sandbox_path)
    _require_ok(diff, action="git diff --cached")
    names = _run_git(["diff", "--cached", "--name-only", baseline], cwd=sandbox_path)
    _require_ok(names, action="git diff --cached --name-only")
    touched = sorted(
        {line.strip() for line in names.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()}
    )
    return diff.stdout.decode("utf-8", errors="replace"), touched


def discard_since(sandbox_path: Path, baseline: str) -> None:
    """Reset the sandbox to ``baseline`` and remove untracked agent edits.

    Never resets to bare ``HEAD`` — always to the given baseline, so a WIP
    overlay applied before the agent ran survives the discard.

    Args:
        sandbox_path: Sandbox checkout to reset.
        baseline: Ref returned by :func:`resolve_pre_agent_baseline`.

    Raises:
        MutationGitError: When the underlying git commands fail.
    """
    reset = _run_git(["reset", "--hard", baseline], cwd=sandbox_path)
    _require_ok(reset, action="git reset --hard")
    clean = _run_git(["clean", "-fd"], cwd=sandbox_path)
    _require_ok(clean, action="git clean -fd")
