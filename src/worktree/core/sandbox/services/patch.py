"""Sandbox patch diff and apply integration service."""

from __future__ import annotations

import re
from pathlib import Path

from worktree.common.filesystem import WorkspacePaths
from worktree.core.db import SandboxesRepository, SandboxRecord, SandboxStatus
from worktree.core.git.exceptions import (
    GitPlumbingTimeoutError,
)
from worktree.core.git.runner import GitRunner
from worktree.core.sandbox.models import (
    SandboxApplyResult,
    SandboxApplyStatus,
    SandboxApplyStrategy,
    SandboxDiffResult,
    SandboxDiffStatus,
)
from worktree.core.sandbox.services.lifecycle import SandboxLifecycle
from worktree.core.sandbox.services.wip import list_wip_paths

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


class SandboxPatch:
    """Orchestrates diff generation, conflict detection, and patch/squash application."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: SandboxesRepository,
        *,
        lifecycle: SandboxLifecycle | None = None,
    ) -> None:
        """Initialize patch service bound to repository root.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit SandboxesRepository instance.
            lifecycle: Optional SandboxLifecycle instance (constructed if None).
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.lifecycle = lifecycle or SandboxLifecycle(self.paths, self.db)

    def _validate_for_diff(
        self,
        sandbox_id: str,
    ) -> tuple[SandboxRecord | None, SandboxDiffResult | None]:
        """Validate sandbox record for diff inspection."""
        record = self.db.get(sandbox_id)
        if record is None:
            return None, SandboxDiffResult(
                status=SandboxDiffStatus.NOT_FOUND,
                sandbox_id=sandbox_id,
                errors=[f"Sandbox '{sandbox_id}' not found."],
            )
        if not Path(record.sandbox_path).is_dir():
            try:
                self.db.reconcile_stale_active(sandbox_id)
            except Exception:
                # Best-effort reconciliation when sandbox directory is missing on disk.
                pass
            return None, SandboxDiffResult(
                status=SandboxDiffStatus.NOT_FOUND,
                sandbox_id=sandbox_id,
                errors=[f"Sandbox '{sandbox_id}' directory is missing on disk."],
            )
        return record, None

    def _validate_for_apply(
        self,
        sandbox_id: str,
    ) -> tuple[SandboxRecord | None, SandboxApplyResult | None]:
        """Validate that sandbox exists in DB, is on disk, and is not already merged."""
        record = self.db.get(sandbox_id)
        if record is None:
            return None, SandboxApplyResult(
                status=SandboxApplyStatus.NOT_FOUND,
                sandbox_id=sandbox_id,
                errors=[f"Sandbox '{sandbox_id}' not found."],
                fixes=["Run `wt sandbox list` to see known sandboxes"],
            )

        if not Path(record.sandbox_path).is_dir():
            try:
                self.db.reconcile_stale_active(sandbox_id)
            except Exception:
                # Best-effort reconciliation when sandbox directory is missing on disk.
                pass
            return None, SandboxApplyResult(
                status=SandboxApplyStatus.NOT_FOUND,
                sandbox_id=sandbox_id,
                errors=[f"Sandbox '{sandbox_id}' directory is missing on disk; reconciled status to 'cleaned'."],
            )

        if record.status == SandboxStatus.MERGED:
            return None, SandboxApplyResult(
                status=SandboxApplyStatus.ALREADY_MERGED,
                sandbox_id=sandbox_id,
                warnings=[f"Sandbox '{sandbox_id}' is already merged."],
            )

        return record, None

    def _check_main_repo_clean(
        self,
        allow_dirty: bool,
        sandbox_id: str,
    ) -> SandboxApplyResult | None:
        """Check if main repository has uncommitted changes."""
        if allow_dirty:
            return None

        dirty_paths = list_wip_paths(self.path)
        if not dirty_paths:
            return None

        return SandboxApplyResult(
            status=SandboxApplyStatus.MAIN_REPO_DIRTY,
            sandbox_id=sandbox_id,
            errors=[f"Cannot apply sandbox {sandbox_id}: main repository has uncommitted changes."],
            fixes=[
                "Commit or stash local changes in the main workspace, or",
                "Pass --allow-dirty to overlay changes anyway",
            ],
        )

    def _collect_delta(
        self,
        sandbox_path: Path,
        base_commit: str,
    ) -> tuple[str, list[str], str]:
        """Collect untracked changes with intent-to-add and compute unified diff, name list, and stat."""
        GitRunner.add_intent_to_add(sandbox_path, target=".")
        diff_text = GitRunner.diff(sandbox_path, base_commit=base_commit, binary=True)
        touched_files = GitRunner.diff_name_only(sandbox_path, base_commit=base_commit)
        stat_text = GitRunner.diff_stat(sandbox_path, base_commit=base_commit)
        return diff_text, touched_files, stat_text

    def _collect_sandbox_changes(
        self,
        record: SandboxRecord,
    ) -> tuple[str, list[str], SandboxApplyResult | None]:
        """Generate unified diff and list of touched files."""
        try:
            diff_text, touched_files, _ = self._collect_delta(Path(record.sandbox_path), record.base_commit)
        except Exception as exc:
            return (
                "",
                [],
                SandboxApplyResult(
                    status=SandboxApplyStatus.GIT_FAILED,
                    sandbox_id=record.id,
                    errors=[f"Failed to generate diff from sandbox: {exc}"],
                ),
            )

        if not touched_files or not diff_text.strip():
            return (
                "",
                [],
                SandboxApplyResult(
                    status=SandboxApplyStatus.EMPTY_DIFF,
                    sandbox_id=record.id,
                    warnings=[f"Sandbox '{record.id}' has no changes to apply."],
                ),
            )

        return diff_text, touched_files, None

    def _verify_patch_cleanliness(
        self,
        diff_text: str,
        sandbox_id: str,
    ) -> tuple[list[str], SandboxApplyResult | None]:
        """Perform dry-run conflict check with git apply --check."""
        try:
            returncode, _, stderr = GitRunner.apply_check(self.path, diff_text, binary=True)
        except GitPlumbingTimeoutError as exc:
            return [], SandboxApplyResult(
                status=SandboxApplyStatus.GIT_FAILED,
                sandbox_id=sandbox_id,
                errors=[f"Git timeout during conflict check: {exc}"],
            )
        except Exception as exc:
            return [], SandboxApplyResult(
                status=SandboxApplyStatus.GIT_FAILED,
                sandbox_id=sandbox_id,
                errors=[f"Git failure during conflict check: {exc}"],
            )

        if returncode != 0:
            conflicts = extract_conflicts(stderr)
            try:
                self.db.update_status(sandbox_id, SandboxStatus.CONFLICT)
            except Exception:
                # Best-effort status update during conflict reporting.
                pass
            conflict_bullets = "\n".join(f"  • {f}" for f in conflicts) if conflicts else f"  {stderr.strip()}"
            return conflicts, SandboxApplyResult(
                status=SandboxApplyStatus.CONFLICT,
                sandbox_id=sandbox_id,
                conflicting_files=conflicts,
                errors=[
                    f"Cannot apply sandbox {sandbox_id}: conflicts detected.\nConflicting files:\n{conflict_bullets}"
                ],
                fixes=[
                    f"Inspect sandbox differences with `wt sandbox diff {sandbox_id}`",
                    "Resolve conflicts in the main workspace or sandbox worktree",
                ],
            )

        return [], None

    def _apply_patch_strategy(
        self,
        diff_text: str,
        strategy: SandboxApplyStrategy,
        message: str | None,
        sandbox_id: str,
    ) -> tuple[str | None, SandboxApplyResult | None]:
        """Apply patch to working directory and optionally commit if squash strategy."""
        try:
            returncode, _, stderr = GitRunner.apply(self.path, diff_text, binary=True)
        except Exception as exc:
            return None, SandboxApplyResult(
                status=SandboxApplyStatus.GIT_FAILED,
                sandbox_id=sandbox_id,
                errors=[f"Git apply failed: {exc}"],
            )

        if returncode != 0:
            err_detail = stderr.strip() or "patch failed"
            return None, SandboxApplyResult(
                status=SandboxApplyStatus.GIT_FAILED,
                sandbox_id=sandbox_id,
                errors=[f"Git apply failed: {err_detail}"],
            )

        if strategy != SandboxApplyStrategy.SQUASH:
            return None, None

        commit_msg = message or f"wt: apply changes from sandbox {sandbox_id}"
        try:
            GitRunner.add_all(self.path)
            GitRunner.commit(self.path, commit_msg)
            commit_sha = GitRunner.rev_parse(self.path, rev="HEAD")
            return commit_sha, None
        except Exception as exc:
            return None, SandboxApplyResult(
                status=SandboxApplyStatus.GIT_FAILED,
                sandbox_id=sandbox_id,
                errors=[f"Git squash commit failed: {exc}"],
            )

    def _cleanup_after_apply(self, record: SandboxRecord, delete: bool) -> tuple[bool, list[str]]:
        """Clean up sandbox worktree and branch if delete requested."""
        if not delete:
            return False, []

        warnings = self.lifecycle.cleanup(record)
        return True, warnings

    def diff(
        self,
        sandbox_id: str,
        *,
        stat: bool = False,
    ) -> SandboxDiffResult:
        """Inspect unified diff or file summary statistics for a sandbox."""
        record, val_err = self._validate_for_diff(sandbox_id)
        if val_err is not None or record is None:
            return val_err or SandboxDiffResult(
                status=SandboxDiffStatus.NOT_FOUND,
                sandbox_id=sandbox_id,
                errors=["Validation failed"],
            )

        try:
            diff_text, touched_files, stat_text = self._collect_delta(Path(record.sandbox_path), record.base_commit)
        except Exception as exc:
            return SandboxDiffResult(
                status=SandboxDiffStatus.GIT_FAILED,
                sandbox_id=sandbox_id,
                errors=[f"Failed to generate diff for sandbox '{sandbox_id}': {exc}"],
            )

        if not touched_files or not diff_text.strip():
            return SandboxDiffResult(
                status=SandboxDiffStatus.EMPTY_DIFF,
                sandbox_id=sandbox_id,
                warnings=[f"Sandbox '{sandbox_id}' has no changes compared to base commit."],
            )

        return SandboxDiffResult(
            status=SandboxDiffStatus.OK,
            sandbox_id=sandbox_id,
            diff_text=diff_text,
            stat_text=stat_text,
            files_changed=touched_files,
        )

    def apply(
        self,
        sandbox_id: str,
        *,
        strategy: SandboxApplyStrategy = SandboxApplyStrategy.PATCH,
        allow_dirty: bool = False,
        dry_run: bool = False,
        delete: bool = False,
        message: str | None = None,
    ) -> SandboxApplyResult:
        """Apply sandbox changes back to main workspace without raising for domain failures."""
        record, val_err = self._validate_for_apply(sandbox_id)
        if val_err is not None or record is None:
            return val_err or SandboxApplyResult(
                status=SandboxApplyStatus.NOT_FOUND,
                sandbox_id=sandbox_id,
                errors=["Validation failed"],
            )

        dirty_err = self._check_main_repo_clean(allow_dirty, sandbox_id)
        if dirty_err is not None:
            return dirty_err

        diff_text, touched_files, coll_err = self._collect_sandbox_changes(record)
        if coll_err is not None:
            return coll_err

        _, conflict_err = self._verify_patch_cleanliness(diff_text, sandbox_id)
        if conflict_err is not None:
            return conflict_err

        if dry_run:
            return SandboxApplyResult(
                status=SandboxApplyStatus.OK,
                sandbox_id=sandbox_id,
                strategy=strategy,
                touched_files=touched_files,
                warnings=["Dry run validation succeeded. No files were modified."],
            )

        commit_sha, apply_err = self._apply_patch_strategy(diff_text, strategy, message, sandbox_id)
        if apply_err is not None:
            return apply_err

        warnings: list[str] = []
        cleaned_up, cleanup_warnings = self._cleanup_after_apply(record, delete)
        warnings.extend(cleanup_warnings)

        try:
            self.db.update_status(sandbox_id, SandboxStatus.MERGED)
        except Exception as exc:
            warnings.append(f"Failed to update database status to 'merged' for sandbox '{sandbox_id}': {exc}")

        return SandboxApplyResult(
            status=SandboxApplyStatus.OK,
            sandbox_id=sandbox_id,
            strategy=strategy,
            touched_files=touched_files,
            commit_sha=commit_sha,
            cleaned_up=cleaned_up,
            warnings=warnings,
        )
