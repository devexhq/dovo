"""Worktree patch diff and apply integration service."""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticLevel, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.db import WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError, GitPlumbingTimeoutError
from dovo.core.git.runner import GitRunner
from dovo.core.git.tree_diff import diff_work_tree
from dovo.core.worktree.exceptions import WorktreeApplyConflict, WorktreeError, recorded_cause
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStrategy,
    WorktreeDiffResult,
    WorktreeRemoval,
)
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.show import find_worktree
from dovo.core.worktree.services.wip import list_wip_paths

_CONFLICT_PATTERNS = (
    re.compile(r"^error:\s+patch failed:\s+([^:]+)"),
    re.compile(r"^error:\s+([^:]+):\s+patch does not apply"),
    re.compile(r"cannot apply binary patch to '([^']+)'"),
    re.compile(r"^error:\s+([^:]+):\s+(?:already exists in working directory|does not exist in index)"),
    re.compile(r"^error:\s+([^:]+):\s+No such file or directory"),
)


def _match_conflict_line(line: str) -> str | None:
    """Return conflicting file path from a single line of git stderr, or None."""
    trimmed = line.strip()
    for pattern in _CONFLICT_PATTERNS:
        match = pattern.search(trimmed)
        if match:
            return match.group(1).strip()
    return None


@contextmanager
def _reraise_git(worktree_id: str, activity: str) -> Generator[None]:
    """Re-raise a GitError from the block as a WorktreeError with its code, chained to recorded_cause(exc).

    The message reads "Git timed out|failed <activity> for worktree '<id>': <exc>".
    """
    try:
        yield
    except GitError as exc:
        verb = "timed out" if isinstance(exc, GitPlumbingTimeoutError) else "failed"
        raise WorktreeError(
            exc.error_code, f"Git {verb} {activity} for worktree '{worktree_id}': {exc}"
        ) from recorded_cause(exc)


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


class WorktreePatch:
    """Orchestrates diff generation, conflict detection, and patch/squash application."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
        *,
        git: GitRunner,
        lifecycle: WorktreeLifecycle,
    ) -> None:
        """Initialize patch service bound to repository root.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit WorktreesRepository instance.
            git: Runner for every git command.
            lifecycle: Lifecycle service whose cleanup removes a worktree after --delete.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.git = git
        self.lifecycle = lifecycle

    def _require_present(self, record: WorktreeRecord, *, operation: str, missing_message: str) -> None:
        """Ensure the row's worktree directory exists.

        A missing directory is reconciled best-effort; a reconcile failure is recorded at debug level for
        operation with context {"worktree_id", "path"}.

        Raises:
            WorktreeError: WORKTREE_MISSING with missing_message.
        """
        if Path(record.worktree_path).is_dir():
            return
        try:
            self.db.reconcile_stale_active(record.id)
        except Exception as exc:
            record_exception(
                exc,
                level=DiagnosticLevel.DEBUG,
                operation=operation,
                context={"worktree_id": record.id, "path": str(record.worktree_path)},
            )
        raise WorktreeError(ErrorCode.WORKTREE_MISSING, missing_message)

    def _check_main_repo_clean(self, allow_dirty: bool, worktree_id: str) -> None:
        """Refuse any uncommitted main-repository change unless allow_dirty.

        Raises:
            WorktreeError: GIT_DIRTY_WORKTREE, or the status read's git code.
        """
        if allow_dirty:
            return

        with _reraise_git(worktree_id, "during conflict check"):
            dirty_paths = list_wip_paths(self.path, git=self.git)
        if not dirty_paths:
            return

        raise WorktreeError(
            ErrorCode.GIT_DIRTY_WORKTREE,
            f"Cannot apply worktree {worktree_id}: main repository has uncommitted changes.",
            [
                Remediation(message="Commit or stash local changes in the main workspace"),
                Remediation(message="Pass '--allow-dirty' to overlay changes anyway"),
            ],
        )

    def _check_squash_overlap(
        self, strategy: WorktreeApplyStrategy, allow_dirty: bool, patch_paths: list[str], worktree_id: str
    ) -> None:
        """Refuse a squash that would commit main-repository changes to a path the patch also touches.

        Raises:
            WorktreeError: GIT_DIRTY_WORKTREE listing the overlapping files, or the status read's git code.
        """
        if strategy != WorktreeApplyStrategy.SQUASH or not allow_dirty:
            return
        with _reraise_git(worktree_id, "during conflict check"):
            dirty = self.git.dirty_paths(self.path)
        overlapping = sorted(set(dirty) & set(patch_paths))
        if not overlapping:
            return
        raise WorktreeError(
            ErrorCode.GIT_DIRTY_WORKTREE,
            f"Cannot squash worktree {worktree_id}: the main repository has uncommitted changes "
            "in files the worktree also changes.\nOverlapping files:\n" + "\n".join(f"  • {p}" for p in overlapping),
            [
                Remediation(message="Commit or stash those files in the main workspace"),
                Remediation(message="Use '--strategy patch' to overlay the changes without committing"),
            ],
        )

    def _collect_delta(
        self,
        worktree_path: Path,
        base_commit: str,
    ) -> tuple[str, list[str], str]:
        """Compute unified diff, touched files and diffstat against base_commit without touching the worktree index."""
        delta = diff_work_tree(worktree_path, base_commit, git=self.git)
        return delta.diff_text, delta.files, delta.stat_text

    def _collect_worktree_changes(
        self, record: WorktreeRecord, strategy: WorktreeApplyStrategy
    ) -> tuple[str, list[str], list[str]]:
        """Return the unified diff, touched files and, for squash, the patch paths.

        Raises:
            WorktreeError: PATCH_EMPTY when there is nothing to apply, or a git code during diff generation.
        """
        worktree_path = Path(record.worktree_path)
        patch_paths: list[str] = []
        with _reraise_git(record.id, "during diff generation"):
            diff_text, touched_files, _ = self._collect_delta(worktree_path, record.base_commit)
            if strategy == WorktreeApplyStrategy.SQUASH and diff_text.strip():
                patch_paths = self.git.patch_paths(worktree_path, diff_text)

        if not touched_files or not diff_text.strip():
            raise WorktreeError(ErrorCode.PATCH_EMPTY, f"Worktree '{record.id}' has no changes to apply.")

        return diff_text, touched_files, patch_paths

    def _verify_patch_cleanliness(self, diff_text: str, worktree_id: str) -> None:
        """Dry-run the patch with git apply --check, marking the worktree conflicted when it names conflicting paths.

        Raises:
            WorktreeError: WORKTREE_APPLY_CONFLICT with WorktreeApplyConflict data when the check fails and its
                stderr names conflicting paths; GIT_FAILED when the check exits non-zero without naming a
                conflicting path, or a git code during the conflict check.
        """
        with _reraise_git(worktree_id, "during conflict check"):
            returncode, _, stderr = self.git.apply_check(self.path, diff_text, binary=True)
        if returncode == 0:
            return

        conflicts = extract_conflicts(stderr)
        if not conflicts:
            raise WorktreeError(
                ErrorCode.GIT_FAILED,
                f"Git failed during conflict check for worktree '{worktree_id}': "
                f"{stderr.strip() or 'patch check failed'}",
            )
        try:
            self.db.update_status(worktree_id, WorktreeStatus.CONFLICT)
        except Exception as exc:
            record_exception(
                exc, level=DiagnosticLevel.DEBUG, operation="worktree.apply", context={"worktree_id": worktree_id}
            )
        conflict_bullets = "\n".join(f"  • {f}" for f in conflicts)
        raise WorktreeError(
            ErrorCode.WORKTREE_APPLY_CONFLICT,
            f"Cannot apply worktree {worktree_id}: conflicts detected.\nConflicting files:\n{conflict_bullets}",
            [
                Remediation(
                    message=f"Inspect worktree differences with 'dovo worktree diff {worktree_id}'",
                    command=f"dovo worktree diff {worktree_id}",
                ),
                Remediation(message="Resolve conflicts in the main workspace or worktree"),
            ],
            data=WorktreeApplyConflict(conflicting_files=tuple(conflicts)),
        )

    def _apply_patch_strategy(
        self,
        diff_text: str,
        strategy: WorktreeApplyStrategy,
        message: str | None,
        worktree_id: str,
        patch_paths: list[str],
    ) -> str | None:
        """Apply the patch and, for squash, commit only the patch paths; return the squash commit SHA or None.

        Raises:
            WorktreeError: GIT_FAILED when git apply exits non-zero, or a git code during patch apply or squash commit.
        """
        with _reraise_git(worktree_id, "during patch apply"):
            returncode, _, stderr = self.git.apply(
                self.path, diff_text, binary=True, index=strategy == WorktreeApplyStrategy.SQUASH
            )
        if returncode != 0:
            raise WorktreeError(
                ErrorCode.GIT_FAILED,
                f"Git failed during patch apply for worktree '{worktree_id}': {stderr.strip() or 'patch failed'}",
            )

        if strategy != WorktreeApplyStrategy.SQUASH:
            return None

        commit_msg = message or f"dovo: apply changes from worktree {worktree_id}"
        with _reraise_git(worktree_id, "during squash commit"):
            self.git.commit(self.path, commit_msg, paths=patch_paths)
            return self.git.rev_parse(self.path, rev="HEAD")

    def diff(self, worktree_id: str, *, stat: bool = False) -> WorktreeDiffResult:
        """Return the worktree's diff against its base commit; a worktree with no changes is an empty success.

        Raises:
            WorktreeError: WORKTREE_NOT_FOUND, WORKTREE_MISSING, or a git failure's code
                (GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING, FS_NOT_FOUND).
        """
        record = find_worktree(self.db, worktree_id)
        self._require_present(
            record, operation="worktree.diff", missing_message=f"Worktree '{worktree_id}' directory is missing on disk."
        )
        with _reraise_git(worktree_id, "while generating the diff"):
            diff_text, touched_files, stat_text = self._collect_delta(Path(record.worktree_path), record.base_commit)

        if not touched_files or not diff_text.strip():
            return WorktreeDiffResult(worktree_id=worktree_id)

        return WorktreeDiffResult(
            worktree_id=worktree_id,
            diff_text=diff_text,
            stat_text=stat_text if stat else "",
            files_changed=touched_files,
        )

    def apply(
        self,
        worktree_id: str,
        *,
        strategy: WorktreeApplyStrategy = WorktreeApplyStrategy.PATCH,
        allow_dirty: bool = False,
        dry_run: bool = False,
        delete: bool = False,
        message: str | None = None,
    ) -> WorktreeApplyResult:
        """Apply worktree changes back to the main workspace and return the applied or dry-run result.

        Raises:
            WorktreeError: WORKTREE_APPLY_CONFLICT with WorktreeApplyConflict data when the patch does not apply
                cleanly, WORKTREE_NOT_FOUND, WORKTREE_ALREADY_MERGED, WORKTREE_MISSING, GIT_DIRTY_WORKTREE,
                PATCH_EMPTY, or a git failure's code (GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING, FS_NOT_FOUND).
        """
        record = find_worktree(self.db, worktree_id)
        if record.status == WorktreeStatus.MERGED:
            raise WorktreeError(ErrorCode.WORKTREE_ALREADY_MERGED, f"Worktree '{worktree_id}' is already merged.")
        self._require_present(
            record,
            operation="worktree.apply",
            missing_message=f"Worktree '{worktree_id}' directory is missing on disk; reconciled status to 'cleaned'.",
        )
        self._check_main_repo_clean(allow_dirty, worktree_id)
        diff_text, touched_files, patch_paths = self._collect_worktree_changes(record, strategy)
        self._check_squash_overlap(strategy, allow_dirty, patch_paths, worktree_id)
        self._verify_patch_cleanliness(diff_text, worktree_id)

        if dry_run:
            return WorktreeApplyResult(
                worktree_id=worktree_id,
                strategy=strategy,
                touched_files=touched_files,
                warnings=["Dry run validation succeeded. No files were modified."],
            )

        commit_sha = self._apply_patch_strategy(diff_text, strategy, message, worktree_id, patch_paths)
        return self._applied_result(record, strategy, touched_files, commit_sha, delete=delete)

    def _applied_result(
        self,
        record: WorktreeRecord,
        strategy: WorktreeApplyStrategy,
        touched_files: list[str],
        commit_sha: str | None,
        *,
        delete: bool,
    ) -> WorktreeApplyResult:
        """Remove the worktree when delete, mark it merged, and build the result carrying the removal's and the status write's warnings and diagnostics."""
        removal = self.lifecycle.cleanup(record) if delete else WorktreeRemoval()
        warnings = list(removal.warnings)
        diagnostics = list(removal.diagnostics)

        try:
            self.db.update_status(record.id, WorktreeStatus.MERGED)
        except Exception as exc:
            warnings.append(f"Failed to update database status to 'merged' for worktree '{record.id}': {exc}")
            diagnostics.extend(
                error_diagnostics(
                    record_exception(
                        exc, level=DiagnosticLevel.ERROR, operation="worktree.apply", context={"worktree_id": record.id}
                    )
                )
            )

        return WorktreeApplyResult(
            worktree_id=record.id,
            strategy=strategy,
            touched_files=touched_files,
            commit_sha=commit_sha,
            cleaned_up=delete,
            warnings=warnings,
            diagnostics=diagnostics,
        )
