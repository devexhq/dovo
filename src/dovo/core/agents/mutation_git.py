"""Worktree-only git helpers for direct-mutation agent providers.

Direct-mutation providers (e.g. Cursor) edit worktree files on disk instead of
returning a diff. These helpers baseline the worktree before the agent runs,
capture what changed, and discard agent edits back to that baseline. A dirty
worktree is baselined by a snapshot commit that no branch points to, built in a
temporary index, so taking a baseline never moves ``HEAD``, a branch or the
worktree's index. Every git command runs through the given GitRunner with
``cwd`` set to the worktree path only, never the user's main worktree, and a
failed one raises the runner's GitError unchanged.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from dovo.core.git import GitRunner, git_command_error

BASELINE_SNAPSHOT_MESSAGE = "dovo: pre-agent baseline"


def resolve_pre_agent_baseline(worktree_path: Path, *, git: GitRunner) -> str:
    """Return a commit SHA that captures worktree state before an agent runs.

    A clean tree baselines to ``HEAD``. A dirty tree (modified, staged, deleted
    or untracked files, e.g. from a ``--wip`` overlay or an earlier step) is
    baselined by a snapshot commit whose tree is the worktree contents, whose
    only parent is ``HEAD`` and which no branch points to. ``HEAD``, every
    branch, the index and ``git status`` are unchanged by the call.

    Args:
        worktree_path: Worktree checkout to baseline.
        git: Runner for every git command.

    Returns:
        A commit SHA usable as a diff or discard target.

    Raises:
        GitError: GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING or GIT_WORKDIR_MISSING when a git command fails; the
            repository is left as it was.
    """
    clean = _is_worktree_clean(worktree_path, git=git)
    head = _head_sha(worktree_path, git=git)
    if clean:
        return head
    return _snapshot_worktree(worktree_path, head, git=git)


def capture_diff_since(worktree_path: Path, baseline: str, *, git: GitRunner) -> tuple[str, list[str]]:
    """Stage all worktree changes and diff them against ``baseline``.

    Covers modified/new/deleted files and any commits the agent made between
    ``baseline`` and the current tree, since the index is compared directly
    against the baseline commit's tree.

    Args:
        worktree_path: Worktree checkout to inspect.
        baseline: Ref returned by :func:`resolve_pre_agent_baseline`.
        git: Runner for every git command.

    Returns:
        Tuple of ``(unified_diff, touched_files)``.

    Raises:
        GitError: GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING or GIT_WORKDIR_MISSING when a git command fails.
    """
    git.run_bytes(["add", "-A"], worktree_path)
    diff = git.run_bytes(["diff", "--cached", baseline], worktree_path)
    names = git.run_bytes(["diff", "--cached", "--name-only", baseline], worktree_path)
    touched = sorted({line.strip() for line in names.decode("utf-8", errors="replace").splitlines() if line.strip()})
    return diff.decode("utf-8", errors="replace"), touched


def capture_binary_patch_since(worktree_path: Path, baseline: str, *, git: GitRunner) -> bytes:
    """Stage all worktree changes and return ``git diff --cached --binary`` against ``baseline`` as raw bytes.

    Raises:
        GitError: GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING or GIT_WORKDIR_MISSING when a git command fails.
    """
    git.run_bytes(["add", "-A"], worktree_path)
    return git.run_bytes(["diff", "--cached", "--binary", baseline], worktree_path)


def discard_since(worktree_path: Path, baseline: str, *, git: GitRunner) -> None:
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
        git: Runner for every git command.

    Raises:
        GitError: GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING or GIT_WORKDIR_MISSING when a git command fails.
    """
    baseline_sha, target = _head_at_baseline(worktree_path, baseline, git=git)
    git.run_bytes(["read-tree", "-u", "--reset", baseline_sha], worktree_path)
    git.run_bytes(["clean", "-fd"], worktree_path)
    git.run_bytes(["update-ref", "HEAD", target], worktree_path)
    if target != baseline_sha:
        git.run_bytes(["read-tree", target], worktree_path)
        _restage_ignored_wip(worktree_path, target, baseline_sha, git=git)


def _restage_ignored_wip(worktree_path: Path, target: str, snapshot: str, *, git: GitRunner) -> None:
    """Stage the paths snapshot added over target that .gitignore matches, which an index reset would untrack."""
    added = git.run_bytes(
        ["diff", "--no-renames", "--name-only", "-z", "--diff-filter=A", target, snapshot], worktree_path
    )
    if not added:
        return

    check_args = ["check-ignore", "--no-index", "-z", "--stdin"]
    ignored = git.exec_bytes(check_args, worktree_path, input_bytes=added)
    if ignored.returncode == 1:
        return
    _require_success(check_args, ignored.returncode, ignored.stdout, ignored.stderr)

    git.run_bytes(["update-index", "--add", "-z", "--stdin"], worktree_path, input_bytes=ignored.stdout)


def _require_success(args: list[str], returncode: int, stdout: bytes, stderr: bytes) -> None:
    """Raise the git package's GIT_FAILED error for `git <args>` when returncode is non-zero."""
    if returncode != 0:
        raise git_command_error(
            args, returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
        )


def _is_worktree_clean(worktree_path: Path, *, git: GitRunner) -> bool:
    """Return whether git reports no changes, without letting the status probe rewrite the index."""
    status = git.run_bytes(["--no-optional-locks", "status", "--porcelain"], worktree_path)
    return not status.strip()


def _head_sha(worktree_path: Path, *, git: GitRunner) -> str:
    """Return the commit SHA HEAD resolves to."""
    return git.run_bytes(["rev-parse", "HEAD"], worktree_path).decode("utf-8").strip()


def _snapshot_worktree(worktree_path: Path, head: str, *, git: GitRunner) -> str:
    """Commit the worktree contents as a child of head through a temporary copy of the index, updating no ref."""
    real_index = _real_index_path(worktree_path, git=git)
    with tempfile.TemporaryDirectory(prefix="dovo-baseline-") as tmp:
        temp_index = Path(tmp) / "index"
        if real_index.exists():
            shutil.copyfile(real_index, temp_index)
        env = {"GIT_INDEX_FILE": str(temp_index)}
        git.run_bytes(["add", "-A"], worktree_path, env=env)
        tree = git.run_bytes(["write-tree"], worktree_path, env=env)

    tree_sha = tree.decode("utf-8").strip()
    snapshot = git.run_bytes(["commit-tree", tree_sha, "-p", head, "-m", BASELINE_SNAPSHOT_MESSAGE], worktree_path)
    return snapshot.decode("utf-8").strip()


def _real_index_path(worktree_path: Path, *, git: GitRunner) -> Path:
    """Return the absolute path of the worktree's index file, as git resolves it for linked worktrees."""
    resolved = git.run_bytes(["rev-parse", "--git-path", "index"], worktree_path)
    index = Path(resolved.decode("utf-8").strip())
    return index if index.is_absolute() else worktree_path / index


def _head_at_baseline(worktree_path: Path, baseline: str, *, git: GitRunner) -> tuple[str, str]:
    """Return (baseline SHA, SHA HEAD held when baseline was taken): the parent of a snapshot, else baseline itself."""
    resolved = git.run_bytes(["rev-parse", "--verify", f"{baseline}^{{commit}}"], worktree_path)
    sha = resolved.decode("utf-8").strip()
    if _is_ancestor_of_head(worktree_path, sha, git=git):
        return sha, sha

    shown = git.run_bytes(["show", "-s", "--format=%P%x00%s", sha], worktree_path)
    parents, _, subject = shown.decode("utf-8").strip().partition("\x00")
    parent_shas = parents.split()
    if subject == BASELINE_SNAPSHOT_MESSAGE and len(parent_shas) == 1:
        return sha, parent_shas[0]
    return sha, sha


def _is_ancestor_of_head(worktree_path: Path, sha: str, *, git: GitRunner) -> bool:
    """Return whether sha is HEAD or an ancestor of it; exit 1 means no, any other non-zero exit raises."""
    args = ["merge-base", "--is-ancestor", sha, "HEAD"]
    check = git.exec_bytes(args, worktree_path)
    if check.returncode == 1:
        return False
    _require_success(args, check.returncode, check.stdout, check.stderr)
    return True
