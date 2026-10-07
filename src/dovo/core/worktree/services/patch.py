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
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStatus,
    WorktreeApplyStrategy,
    WorktreeDiffResult,
    WorktreeDiffStatus,
)
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
        lifecycle: WorktreeLifecycle | None = None,
    ) -> None:
        """Initialize patch service bound to repository root.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit WorktreesRepository instance.
            lifecycle: Optional WorktreeLifecycle instance (constructed if None).
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.lifecycle = lifecycle or WorktreeLifecycle(self.paths, self.db)

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
            except Exception:
                # Best-effort reconciliation when worktree directory is missing on disk.
                pass
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
            except Exception:
                # Best-effort reconciliation when worktree directory is missing on disk.
                pass
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
        """Check if main repository has uncommitted changes."""
        if allow_dirty:
            return None

        dirty_paths = list_wip_paths(self.path)
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

    def _collect_delta(
        self,
        worktree_path: Path,
        base_commit: str,
    ) -> tuple[str, list[str], str]:
        """Collect untracked changes with intent-to-add and compute unified diff, name list, and stat."""
        GitRunner.add_intent_to_add(worktree_path, target=".")
        diff_text = GitRunner.diff(worktree_path, base_commit=base_commit, binary=True)
        touched_files = GitRunner.diff_name_only(worktree_path, base_commit=base_commit)
        stat_text = GitRunner.diff_stat(worktree_path, base_commit=base_commit)
        return diff_text, touched_files, stat_text

    def _collect_worktree_changes(
        self,
        record: WorktreeRecord,
    ) -> tuple[str, list[str], WorktreeApplyResult | None]:
        """Generate unified diff and list of touched files."""
        try:
            diff_text, touched_files, _ = self._collect_delta(Path(record.worktree_path), record.base_commit)
        except Exception as exc:
            return (
                "",
                [],
                WorktreeApplyResult(
                    status=WorktreeApplyStatus.GIT_FAILED,
                    worktree_id=record.id,
                    errors=[f"Failed to generate diff from worktree: {exc}"],
                ),
            )

        if not touched_files or not diff_text.strip():
            return (
                "",
                [],
                WorktreeApplyResult(
                    status=WorktreeApplyStatus.EMPTY_DIFF,
                    worktree_id=record.id,
                    warnings=[f"Worktree '{record.id}' has no changes to apply."],
                ),
            )

        return diff_text, touched_files, None

    def _verify_patch_cleanliness(
        self,
        diff_text: str,
        worktree_id: str,
    ) -> tuple[list[str], WorktreeApplyResult | None]:
        """Perform dry-run conflict check with git apply --check."""
        try:
            returncode, _, stderr = GitRunner.apply_check(self.path, diff_text, binary=True)
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
                errors=[f"Git failure during conflict check: {exc}"],
            )

        if returncode != 0:
            conflicts = extract_conflicts(stderr)
            try:
                self.db.update_status(worktree_id, WorktreeStatus.CONFLICT)
            except Exception:
                # Best-effort status update during conflict reporting.
                pass
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
    ) -> tuple[str | None, WorktreeApplyResult | None]:
        """Apply patch to working directory and optionally commit if squash strategy."""
        try:
            returncode, _, stderr = GitRunner.apply(self.path, diff_text, binary=True)
        except Exception as exc:
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[f"Git apply failed: {exc}"],
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
            GitRunner.add_all(self.path)
            GitRunner.commit(self.path, commit_msg)
            commit_sha = GitRunner.rev_parse(self.path, rev="HEAD")
            return commit_sha, None
        except Exception as exc:
            return None, WorktreeApplyResult(
                status=WorktreeApplyStatus.GIT_FAILED,
                worktree_id=worktree_id,
                errors=[f"Git squash commit failed: {exc}"],
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
                errors=[f"Failed to generate diff for worktree '{worktree_id}': {exc}"],
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

        diff_text, touched_files, coll_err = self._collect_worktree_changes(record)
        if coll_err is not None:
            return coll_err

        _, conflict_err = self._verify_patch_cleanliness(diff_text, worktree_id)
        if conflict_err is not None:
            return conflict_err

        if dry_run:
            return WorktreeApplyResult(
                status=WorktreeApplyStatus.OK,
                worktree_id=worktree_id,
                strategy=strategy,
                touched_files=touched_files,
                warnings=["Dry run validation succeeded. No files were modified."],
            )

        commit_sha, apply_err = self._apply_patch_strategy(diff_text, strategy, message, worktree_id)
        if apply_err is not None:
            return apply_err

        warnings: list[str] = []
        cleaned_up, cleanup_warnings = self._cleanup_after_apply(record, delete)
        warnings.extend(cleanup_warnings)

        try:
            self.db.update_status(worktree_id, WorktreeStatus.MERGED)
        except Exception as exc:
            warnings.append(f"Failed to update database status to 'merged' for worktree '{worktree_id}': {exc}")

        return WorktreeApplyResult(
            status=WorktreeApplyStatus.OK,
            worktree_id=worktree_id,
            strategy=strategy,
            touched_files=touched_files,
            commit_sha=commit_sha,
            cleaned_up=cleaned_up,
            warnings=warnings,
        )
