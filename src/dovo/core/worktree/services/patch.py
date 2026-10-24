"""Worktree patch diff and apply integration service."""

from __future__ import annotations

import re
from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreeRecord, WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import (
    GitPlumbingTimeoutError,
)
from dovo.core.git.runner import GitRunner
from dovo.core.git.tree_diff import diff_work_tree
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStatus,
    WorktreeApplyStrategy,
    WorktreeDiffResult,
    WorktreeDiffStatus,
)
from dovo.core.worktree.services.failure_log import logged_failure, record_fallback
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.wip import list_wip_paths

_CONFLICT_PATTERNS = (
    re.compile(r"^error:\s+patch failed:\s+([^:]+)"),
    re.compile(r"^error:\s+([^:]+):\s+patch does not apply"),
    re.compile(r"cannot apply binary patch to '([^']+)'"),
    re.compile(r"^error:\s+([^:]+):\s+(?:already exists in working directory|does not exist in index)"),
)


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

    def _validate_for_diff(
        self,
        worktree_id: str,
    ) -> tuple[WorktreeRecord | None, WorktreeDiffResult | None]:
        """Validate worktree record for diff inspection."""
        record = self.db.get(worktree_id)
        if record is None:
            return None, WorktreeDiffResult(
                status=WorktreeDiffStatus.NOT_FOUND,
                worktree_id=worktree_id,
                errors=[f"Worktree '{worktree_id}' not found."],
            )
        if not Path(record.worktree_path).is_dir():
            try:
                self.db.reconcile_stale_active(worktree_id)
            except Exception as exc:
                record_fallback(
                    exc,
                    operation="worktree.diff",
                    context={"worktree_id": worktree_id, "path": str(record.worktree_path)},
                )
            return None, WorktreeDiffResult(
                status=WorktreeDiffStatus.NOT_FOUND,
                worktree_id=worktree_id,
                errors=[f"Worktree '{worktree_id}' directory is missing on disk."],
            )
        return record, None

    def _validate_for_apply(
        self,
        worktree_id: str,
    ) -> tuple[WorktreeRecord | None, WorktreeApplyResult | None]:
        """Validate that worktree exists in DB, is on disk, and is not already merged."""
        record = self.db.get(worktree_id)
        if record is None:
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.NOT_FOUND,
                worktree_id=worktree_id,
                errors=[f"Worktree '{worktree_id}' not found."],
                fixes=["Run `dovo worktree list` to see known worktrees"],
            )

        if not Path(record.worktree_path).is_dir():
            try:
                self.db.reconcile_stale_active(worktree_id)
            except Exception as exc:
                record_fallback(
                    exc,
                    operation="worktree.apply",
                    context={"worktree_id": worktree_id, "path": str(record.worktree_path)},
                )
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.NOT_FOUND,
                worktree_id=worktree_id,
                errors=[f"Worktree '{worktree_id}' directory is missing on disk; reconciled status to 'cleaned'."],
            )

        if record.status == WorktreeStatus.MERGED:
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.ALREADY_MERGED,
                worktree_id=worktree_id,
                warnings=[f"Worktree '{worktree_id}' is already merged."],
            )

        return record, None

    def _check_main_repo_clean(
        self,
        allow_dirty: bool,
        worktree_id: str,
    ) -> WorktreeApplyResult | None:
        """Refuse any uncommitted main-repository change; this blanket check applies only without --allow-dirty."""
        if allow_dirty:
            return None

        dirty_paths = list_wip_paths(self.path, git=self.git)
        if not dirty_paths:
            return None

        return WorktreeApplyResult(
            status=WorktreeApplyStatus.MAIN_REPO_DIRTY,
            worktree_id=worktree_id,
            errors=[f"Cannot apply worktree {worktree_id}: main repository has uncommitted changes."],
            fixes=[
                "Commit or stash local changes in the main workspace, or",
                "Pass --allow-dirty to overlay changes anyway",
            ],
        )

    def _check_squash_overlap(
        self,
        strategy: WorktreeApplyStrategy,
        allow_dirty: bool,
        patch_paths: list[str],
        worktree_id: str,
    ) -> WorktreeApplyResult | None:
        """Refuse a squash that would commit main-repository changes to a path the patch also touches."""
        if strategy != WorktreeApplyStrategy.SQUASH or not allow_dirty:
            return None
        try:
            dirty = self.git.dirty_paths(self.path)
        except Exception as exc:
            return WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                strategy=strategy,
                errors=[
                    logged_failure(
                        f"Failed to read main repository status: {exc}",
                        exc,
                        operation="worktree.apply",
                        context={"worktree_id": worktree_id},
                    )
                ],
            )
        overlapping = sorted(set(dirty) & set(patch_paths))
        if not overlapping:
            return None
        return WorktreeApplyResult(
            status=WorktreeApplyStatus.MAIN_REPO_DIRTY,
            worktree_id=worktree_id,
            strategy=strategy,
            errors=[
                f"Cannot squash worktree {worktree_id}: the main repository has uncommitted changes "
                "in files the worktree also changes.\nOverlapping files:\n" + "\n".join(f"  • {p}" for p in overlapping)
            ],
            fixes=[
                "Commit or stash those files in the main workspace, or",
                "Use --strategy patch to overlay the changes without committing",
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
        self,
        record: WorktreeRecord,
        strategy: WorktreeApplyStrategy,
    ) -> tuple[str, list[str], list[str], WorktreeApplyResult | None]:
        """Generate unified diff, touched files, and for squash the patch paths (both sides of a rename)."""
        worktree_path = Path(record.worktree_path)
        patch_paths: list[str] = []
        try:
            diff_text, touched_files, _ = self._collect_delta(worktree_path, record.base_commit)
            if strategy == WorktreeApplyStrategy.SQUASH and diff_text.strip():
                patch_paths = self.git.patch_paths(worktree_path, diff_text)
        except Exception as exc:
            return (
                "",
                [],
                [],
                WorktreeApplyResult(
                    status=WorktreeApplyStatus.GIT_FAILED,
                    worktree_id=record.id,
                    errors=[
                        logged_failure(
                            f"Failed to generate diff from worktree: {exc}",
                            exc,
                            operation="worktree.apply",
                            context={"worktree_id": record.id, "path": str(record.worktree_path)},
                        )
                    ],
                ),
            )

        if not touched_files or not diff_text.strip():
            return (
                "",
                [],
                [],
                WorktreeApplyResult(
                    status=WorktreeApplyStatus.EMPTY_DIFF,
                    worktree_id=record.id,
                    warnings=[f"Worktree '{record.id}' has no changes to apply."],
                ),
            )

        return diff_text, touched_files, patch_paths, None

    def _verify_patch_cleanliness(
        self,
        diff_text: str,
        worktree_id: str,
    ) -> tuple[list[str], WorktreeApplyResult | None]:
        """Perform dry-run conflict check with git apply --check."""
        try:
            returncode, _, stderr = self.git.apply_check(self.path, diff_text, binary=True)
        except GitPlumbingTimeoutError as exc:
            return [], WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[f"Git timeout during conflict check: {exc}"],
            )
        except Exception as exc:
            return [], WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[
                    logged_failure(
                        f"Git failure during conflict check: {exc}",
                        exc,
                        operation="worktree.apply",
                        context={"worktree_id": worktree_id},
                    )
                ],
            )

        if returncode != 0:
            conflicts = extract_conflicts(stderr)
            try:
                self.db.update_status(worktree_id, WorktreeStatus.CONFLICT)
            except Exception as exc:
                record_fallback(exc, operation="worktree.apply", context={"worktree_id": worktree_id})
            conflict_bullets = "\n".join(f"  • {f}" for f in conflicts) if conflicts else f"  {stderr.strip()}"
            return conflicts, WorktreeApplyResult(
                status=WorktreeApplyStatus.CONFLICT,
                worktree_id=worktree_id,
                conflicting_files=conflicts,
                errors=[
                    f"Cannot apply worktree {worktree_id}: conflicts detected.\nConflicting files:\n{conflict_bullets}"
                ],
                fixes=[
                    f"Inspect worktree differences with `dovo worktree diff {worktree_id}`",
                    "Resolve conflicts in the main workspace or worktree",
                ],
            )

        return [], None

    def _apply_patch_strategy(
        self,
        diff_text: str,
        strategy: WorktreeApplyStrategy,
        message: str | None,
        worktree_id: str,
        patch_paths: list[str],
    ) -> tuple[str | None, WorktreeApplyResult | None]:
        """Apply patch to working directory and, for squash, commit only the patch paths."""
        try:
            returncode, _, stderr = self.git.apply(
                self.path, diff_text, binary=True, index=strategy == WorktreeApplyStrategy.SQUASH
            )
        except Exception as exc:
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[
                    logged_failure(
                        f"Git apply failed: {exc}",
                        exc,
                        operation="worktree.apply",
                        context={"worktree_id": worktree_id},
                    )
                ],
            )

        if returncode != 0:
            err_detail = stderr.strip() or "patch failed"
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[f"Git apply failed: {err_detail}"],
            )

        if strategy != WorktreeApplyStrategy.SQUASH:
            return None, None

        commit_msg = message or f"dovo: apply changes from worktree {worktree_id}"
        try:
            self.git.commit(self.path, commit_msg, paths=patch_paths)
            commit_sha = self.git.rev_parse(self.path, rev="HEAD")
            return commit_sha, None
        except Exception as exc:
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[
                    logged_failure(
                        f"Git squash commit failed: {exc}",
                        exc,
                        operation="worktree.apply",
                        context={"worktree_id": worktree_id},
                    )
                ],
            )

    def _cleanup_after_apply(self, record: WorktreeRecord, delete: bool) -> tuple[bool, list[str]]:
        """Clean up worktree and branch if delete requested."""
        if not delete:
            return False, []

        warnings = self.lifecycle.cleanup(record)
        return True, warnings

    def diff(
        self,
        worktree_id: str,
        *,
        stat: bool = False,
    ) -> WorktreeDiffResult:
        """Inspect unified diff or file summary statistics for a worktree."""
        record, val_err = self._validate_for_diff(worktree_id)
        if val_err is not None or record is None:
            return val_err or WorktreeDiffResult(
                status=WorktreeDiffStatus.NOT_FOUND,
                worktree_id=worktree_id,
                errors=["Validation failed"],
            )

        try:
            diff_text, touched_files, stat_text = self._collect_delta(Path(record.worktree_path), record.base_commit)
        except Exception as exc:
            return WorktreeDiffResult(
                status=WorktreeDiffStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[
                    logged_failure(
                        f"Failed to generate diff for worktree '{worktree_id}': {exc}",
                        exc,
                        operation="worktree.diff",
                        context={"worktree_id": worktree_id, "path": str(record.worktree_path)},
                    )
                ],
            )

        if not touched_files or not diff_text.strip():
            return WorktreeDiffResult(
                status=WorktreeDiffStatus.EMPTY_DIFF,
                worktree_id=worktree_id,
                warnings=[f"Worktree '{worktree_id}' has no changes compared to base commit."],
            )

        return WorktreeDiffResult(
            status=WorktreeDiffStatus.OK,
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
        """Apply worktree changes back to main workspace without raising for domain failures."""
        record, val_err = self._validate_for_apply(worktree_id)
        if val_err is not None or record is None:
            return val_err or WorktreeApplyResult(
                status=WorktreeApplyStatus.NOT_FOUND,
                worktree_id=worktree_id,
                errors=["Validation failed"],
            )

        dirty_err = self._check_main_repo_clean(allow_dirty, worktree_id)
        if dirty_err is not None:
            return dirty_err

        diff_text, touched_files, patch_paths, coll_err = self._collect_worktree_changes(record, strategy)
        if coll_err is not None:
            return coll_err

        pre_err = (
            self._check_squash_overlap(strategy, allow_dirty, patch_paths, worktree_id)
            or self._verify_patch_cleanliness(diff_text, worktree_id)[1]
        )
        if pre_err is not None:
            return pre_err

        if dry_run:
            return WorktreeApplyResult(
                status=WorktreeApplyStatus.OK,
                worktree_id=worktree_id,
                strategy=strategy,
                touched_files=touched_files,
                warnings=["Dry run validation succeeded. No files were modified."],
            )

        commit_sha, apply_err = self._apply_patch_strategy(diff_text, strategy, message, worktree_id, patch_paths)
        if apply_err is not None:
            return apply_err

        warnings: list[str] = []
        cleaned_up, cleanup_warnings = self._cleanup_after_apply(record, delete)
        warnings.extend(cleanup_warnings)

        try:
            self.db.update_status(worktree_id, WorktreeStatus.MERGED)
        except Exception as exc:
            warnings.append(
                logged_failure(
                    f"Failed to update database status to 'merged' for worktree '{worktree_id}': {exc}",
                    exc,
                    operation="worktree.apply",
                    context={"worktree_id": worktree_id},
                )
            )

        return WorktreeApplyResult(
            status=WorktreeApplyStatus.OK,
            worktree_id=worktree_id,
            strategy=strategy,
            touched_files=touched_files,
            commit_sha=commit_sha,
            cleaned_up=cleaned_up,
            warnings=warnings,
        )
