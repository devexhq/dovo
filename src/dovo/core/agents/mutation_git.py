"""Worktree-only git helpers for direct-mutation agent providers.

Direct-mutation providers (e.g. Cursor) edit worktree files on disk instead of
returning a diff. These helpers baseline the worktree before the agent runs,
capture what changed, and discard agent edits back to that baseline. A dirty
worktree is baselined by a snapshot commit that no branch points to, built in a
temporary index, so taking a baseline never moves ``HEAD``, a branch or the
worktree's index. All operations run with ``cwd`` set to the worktree path
only, never the user's main worktree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from dovo.common.constants import GIT_SUBPROCESS_TIMEOUT_SECONDS
from dovo.common.error_codes import ErrorCode
from dovo.core.agents.exceptions import AgentError

BASELINE_SNAPSHOT_MESSAGE = "dovo: pre-agent baseline"


class MutationGitError(AgentError):
    """Raised when a worktree git operation for a direct-mutation provider fails."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.AGENT_MUTATION_GIT_FAILED, message)


def _run_git(
    args: list[str], *, cwd: Path, env: dict[str, str] | None = None, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run git command in subprocess under cwd with timeout handling, using env and feeding stdin when given."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            input=stdin,
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


def resolve_pre_agent_baseline(worktree_path: Path) -> str:
    """Return a commit SHA that captures worktree state before an agent runs.

    A clean tree baselines to ``HEAD``. A dirty tree (modified, staged, deleted
    or untracked files, e.g. from a ``--wip`` overlay or an earlier step) is
    baselined by a snapshot commit whose tree is the worktree contents, whose
    only parent is ``HEAD`` and which no branch points to. ``HEAD``, every
    branch, the index and ``git status`` are unchanged by the call.

    Args:
        worktree_path: Worktree checkout to baseline.

    Returns:
        A commit SHA usable as a diff or discard target.

    Raises:
        MutationGitError: When the underlying git commands fail; the
            repository is left as it was.
    """
    clean = _is_worktree_clean(worktree_path)
    head = _head_sha(worktree_path)
    if clean:
        return head
    return _snapshot_worktree(worktree_path, head)


def capture_diff_since(worktree_path: Path, baseline: str) -> tuple[str, list[str]]:
    """Stage all worktree changes and diff them against ``baseline``.

    Covers modified/new/deleted files and any commits the agent made between
    ``baseline`` and the current tree, since the index is compared directly
    against the baseline commit's tree.

    Args:
        worktree_path: Worktree checkout to inspect.
        baseline: Ref returned by :func:`resolve_pre_agent_baseline`.

    Returns:
        Tuple of ``(unified_diff, touched_files)``.

    Raises:
        MutationGitError: When the underlying git commands fail.
    """
    add = _run_git(["add", "-A"], cwd=worktree_path)
    _require_ok(add, action="git add -A (capture diff)")
    diff = _run_git(["diff", "--cached", baseline], cwd=worktree_path)
    _require_ok(diff, action="git diff --cached")
    names = _run_git(["diff", "--cached", "--name-only", baseline], cwd=worktree_path)
    _require_ok(names, action="git diff --cached --name-only")
    touched = sorted(
        {line.strip() for line in names.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()}
    )
    return diff.stdout.decode("utf-8", errors="replace"), touched


def capture_binary_patch_since(worktree_path: Path, baseline: str) -> bytes:
    """Stage all worktree changes and return ``git diff --cached --binary`` against ``baseline`` as raw bytes.

    Raises:
        MutationGitError: When the underlying git commands fail.
    """
    add = _run_git(["add", "-A"], cwd=worktree_path)
    _require_ok(add, action="git add -A (capture patch)")
    diff = _run_git(["diff", "--cached", "--binary", baseline], cwd=worktree_path)
    _require_ok(diff, action="git diff --cached --binary")

    return diff.stdout


def discard_since(worktree_path: Path, baseline: str) -> None:
    """Restore the worktree to ``baseline`` and move ``HEAD`` back to where it was when the baseline was taken.

    Every file returns to its content in ``baseline`` and files absent from it
    are removed. ``HEAD`` and its branch return to ``baseline`` for a clean-tree
    baseline, or to the snapshot's parent for a dirty-tree one, dropping any
    commits the agent made; WIP that predated the agent stays uncommitted. The
    index matches the restored ``HEAD``, except that WIP files matched by
    ``.gitignore`` (force-added before the baseline) stay staged, so git keeps
    tracking them.

    Args:
        worktree_path: Worktree checkout to restore.
        baseline: Commit returned by :func:`resolve_pre_agent_baseline`.

    Raises:
        MutationGitError: When the underlying git commands fail.
    """
    baseline_sha, target = _head_at_baseline(worktree_path, baseline)
    restore = _run_git(["read-tree", "-u", "--reset", baseline_sha], cwd=worktree_path)
    _require_ok(restore, action="git read-tree -u --reset")
    clean = _run_git(["clean", "-fd"], cwd=worktree_path)
    _require_ok(clean, action="git clean -fd")
    move_head = _run_git(["update-ref", "HEAD", target], cwd=worktree_path)
    _require_ok(move_head, action="git update-ref HEAD")
    if target != baseline_sha:
        index = _run_git(["read-tree", target], cwd=worktree_path)
        _require_ok(index, action="git read-tree (restore index)")
        _restage_ignored_wip(worktree_path, target, baseline_sha)


def _restage_ignored_wip(worktree_path: Path, target: str, snapshot: str) -> None:
    """Stage the paths snapshot added over target that .gitignore matches, which an index reset would untrack."""
    added = _run_git(
        ["diff", "--no-renames", "--name-only", "-z", "--diff-filter=A", target, snapshot], cwd=worktree_path
    )
    _require_ok(added, action="git diff --name-only (snapshot additions)")
    if not added.stdout:
        return

    ignored = _run_git(["check-ignore", "--no-index", "-z", "--stdin"], cwd=worktree_path, stdin=added.stdout)
    if ignored.returncode == 1:
        return
    _require_ok(ignored, action="git check-ignore")

    stage = _run_git(["update-index", "--add", "-z", "--stdin"], cwd=worktree_path, stdin=ignored.stdout)
    _require_ok(stage, action="git update-index --add (ignored WIP)")


def _is_worktree_clean(worktree_path: Path) -> bool:
    """Return whether git reports no changes, without letting the status probe rewrite the index."""
    status = _run_git(["--no-optional-locks", "status", "--porcelain"], cwd=worktree_path)
    _require_ok(status, action="git status")
    return not status.stdout.strip()


def _head_sha(worktree_path: Path) -> str:
    """Return the commit SHA HEAD resolves to."""
    head = _run_git(["rev-parse", "HEAD"], cwd=worktree_path)
    _require_ok(head, action="git rev-parse HEAD")
    return head.stdout.decode("utf-8").strip()


def _snapshot_worktree(worktree_path: Path, head: str) -> str:
    """Commit the worktree contents as a child of head through a temporary copy of the index, updating no ref."""
    real_index = _real_index_path(worktree_path)
    with tempfile.TemporaryDirectory(prefix="dovo-baseline-") as tmp:
        temp_index = Path(tmp) / "index"
        if real_index.exists():
            shutil.copyfile(real_index, temp_index)
        env = {**os.environ, "GIT_INDEX_FILE": str(temp_index)}
        add = _run_git(["add", "-A"], cwd=worktree_path, env=env)
        _require_ok(add, action="git add -A (pre-agent baseline)")
        tree = _run_git(["write-tree"], cwd=worktree_path, env=env)
        _require_ok(tree, action="git write-tree (pre-agent baseline)")

    tree_sha = tree.stdout.decode("utf-8").strip()
    snapshot = _run_git(
        ["commit-tree", tree_sha, "-p", head, "-m", BASELINE_SNAPSHOT_MESSAGE],
        cwd=worktree_path,
    )
    _require_ok(snapshot, action="git commit-tree (pre-agent baseline)")
    return snapshot.stdout.decode("utf-8").strip()


def _real_index_path(worktree_path: Path) -> Path:
    """Return the absolute path of the worktree's index file, as git resolves it for linked worktrees."""
    resolved = _run_git(["rev-parse", "--git-path", "index"], cwd=worktree_path)
    _require_ok(resolved, action="git rev-parse --git-path index")
    index = Path(resolved.stdout.decode("utf-8").strip())
    return index if index.is_absolute() else worktree_path / index


def _head_at_baseline(worktree_path: Path, baseline: str) -> tuple[str, str]:
    """Return (baseline SHA, SHA HEAD held when baseline was taken): the parent of a snapshot, else baseline itself."""
    resolved = _run_git(["rev-parse", "--verify", f"{baseline}^{{commit}}"], cwd=worktree_path)
    _require_ok(resolved, action="git rev-parse baseline")
    sha = resolved.stdout.decode("utf-8").strip()
    if _is_ancestor_of_head(worktree_path, sha):
        return sha, sha

    shown = _run_git(["show", "-s", "--format=%P%x00%s", sha], cwd=worktree_path)
    _require_ok(shown, action="git show baseline")
    parents, _, subject = shown.stdout.decode("utf-8").strip().partition("\x00")
    parent_shas = parents.split()
    if subject == BASELINE_SNAPSHOT_MESSAGE and len(parent_shas) == 1:
        return sha, parent_shas[0]
    return sha, sha


def _is_ancestor_of_head(worktree_path: Path, sha: str) -> bool:
    """Return whether sha is HEAD or an ancestor of it; exit 1 means no, any other non-zero exit raises."""
    check = _run_git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd=worktree_path)
    if check.returncode == 1:
        return False
    _require_ok(check, action="git merge-base --is-ancestor")
    return True
