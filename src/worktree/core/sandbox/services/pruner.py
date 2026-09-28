"""Safe prune execution service for stale sandboxes and resources."""

from __future__ import annotations

import shutil
from pathlib import Path

from worktree.common.filesystem.models import RepositoryPaths
from worktree.common.lock import LockTimeoutError, WorkspaceLock
from worktree.core.db import RunsRepository, SandboxesRepository, SandboxStatus
from worktree.core.git.runner import GitRunner
from worktree.core.sandbox.models import (
    PruneAction,
    PrunedItem,
    SandboxDetectionStatus,
    SandboxPruneResult,
    SandboxPruneStatus,
    StaleSandboxCategory,
    StaleSandboxItem,
)
from worktree.core.sandbox.services.detector import SandboxDetector


class SandboxPruner:
    """Safe cleanup executor for stale sandboxes, orphaned directories, and temporary branches."""

    def __init__(
        self,
        path: Path,
        db: SandboxesRepository,
        runs_db: RunsRepository | None = None,
    ) -> None:
        """Initialize pruner bound to repository root and database.

        Args:
            path: Repository root directory.
            db: SandboxesRepository instance.
            runs_db: Optional RunsRepository instance for run liveness checks.
        """
        self.path = path.expanduser().resolve()
        self.db = db
        self.runs_db = runs_db
        self.detector = SandboxDetector(self.path, self.db, runs_db=self.runs_db)

    def _prune_stale_worktree_ref(self, item: StaleSandboxItem, *, dry_run: bool) -> PrunedItem:
        """Prune administrative worktree registrations."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            )

        try:
            GitRunner.worktree_prune(self.path)
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                reason=item.reason,
            )
        except Exception as exc:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                path=item.path,
                branch_name=item.branch_name,
                reason=item.reason,
                error=f"Failed to prune git worktree ref: {exc}",
            )

    def _remove_orphaned_dir(self, dir_path: Path) -> str | None:
        """Attempt removal via git worktree remove and rmtree fallback."""
        if not dir_path.exists():
            return None
        try:
            GitRunner.worktree_remove(self.path, dir_path, force=True)
            return None
        except Exception:
            try:
                shutil.rmtree(dir_path)
                return None
            except Exception as rm_exc:
                return f"Failed to remove directory '{dir_path}': {rm_exc}"

    def _cleanup_orphaned_dir_and_db(self, item: StaleSandboxItem) -> list[str]:
        """Perform disk deletion and database status update for an orphaned directory."""
        errors: list[str] = []
        if item.path is not None:
            dir_err = self._remove_orphaned_dir(item.path)
            if dir_err:
                errors.append(dir_err)

        if item.session_id is not None:
            try:
                self.db.update_status(item.session_id, SandboxStatus.CLEANED)
            except Exception as db_exc:
                errors.append(f"Failed to update database status for '{item.session_id}': {db_exc}")

        try:
            GitRunner.worktree_prune(self.path)
        except Exception:
            pass

        return errors

    def _prune_orphaned_directory(
        self,
        item: StaleSandboxItem,
        *,
        dry_run: bool,
        force: bool,
    ) -> PrunedItem:
        """Remove unindexed or non-active sandbox directory safely."""
        if item.is_dirty and not force:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.SKIPPED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=f"Orphaned directory '{item.identifier}' contains uncommitted changes; use --force to delete",
            )

        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            )

        errors = self._cleanup_orphaned_dir_and_db(item)
        if errors:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=item.reason,
                error="; ".join(errors),
            )

        return PrunedItem(
            category=item.category,
            identifier=item.identifier,
            action=PruneAction.PRUNED,
            path=item.path,
            branch_name=item.branch_name,
            session_id=item.session_id,
            reason=item.reason,
        )

    def _prune_stale_db_record(self, item: StaleSandboxItem, *, dry_run: bool) -> PrunedItem:
        """Reconcile active database rows missing on disk."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            )

        try:
            self.db.update_status(item.identifier, SandboxStatus.CLEANED)
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=item.reason,
            )
        except Exception as exc:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                path=item.path,
                branch_name=item.branch_name,
                session_id=item.session_id,
                reason=item.reason,
                error=f"Failed to update database status for '{item.identifier}': {exc}",
            )

    def _prune_stale_branch(self, item: StaleSandboxItem, *, dry_run: bool) -> PrunedItem:
        """Delete unlinked temporary sandbox branches."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                branch_name=item.branch_name,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            )

        try:
            GitRunner.branch_delete(self.path, item.identifier, force=True)
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                branch_name=item.branch_name,
                reason=item.reason,
            )
        except Exception as exc:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.FAILED,
                branch_name=item.branch_name,
                reason=item.reason,
                error=f"Failed to delete branch '{item.identifier}': {exc}",
            )

    def _process_item(
        self,
        item: StaleSandboxItem,
        *,
        dry_run: bool,
        force: bool,
    ) -> PrunedItem:
        """Route stale item to the appropriate category handler."""
        if item.category == StaleSandboxCategory.STALE_WORKTREE_REF:
            return self._prune_stale_worktree_ref(item, dry_run=dry_run)
        elif item.category == StaleSandboxCategory.ORPHANED_DIRECTORY:
            return self._prune_orphaned_directory(item, dry_run=dry_run, force=force)
        elif item.category == StaleSandboxCategory.STALE_DB_RECORD:
            return self._prune_stale_db_record(item, dry_run=dry_run)
        elif item.category == StaleSandboxCategory.STALE_BRANCH:
            return self._prune_stale_branch(item, dry_run=dry_run)
        return PrunedItem(
            category=item.category,
            identifier=item.identifier,
            action=PruneAction.SKIPPED,
            reason=f"Unknown category '{item.category}'",
        )

    def _execute_prune(
        self,
        *,
        dry_run: bool,
        force: bool,
    ) -> SandboxPruneResult:
        """Internal execution routine following stale resource detection."""
        detection = self.detector.detect()
        if not detection.ok:
            status = (
                SandboxPruneStatus.GIT_FAILED
                if detection.status == SandboxDetectionStatus.GIT_FAILED
                else SandboxPruneStatus.ERROR
            )
            return SandboxPruneResult(
                status=status,
                dry_run=dry_run,
                force=force,
                errors=detection.errors,
                warnings=detection.warnings,
            )

        processed_items: list[PrunedItem] = []
        errors: list[str] = []

        for stale_item in detection.items:
            res = self._process_item(stale_item, dry_run=dry_run, force=force)
            processed_items.append(res)
            if res.error:
                errors.append(res.error)

        status = SandboxPruneStatus.PARTIAL_SUCCESS if errors else SandboxPruneStatus.OK

        return SandboxPruneResult(
            status=status,
            dry_run=dry_run,
            force=force,
            items=processed_items,
            errors=errors,
            warnings=detection.warnings,
        )

    def prune(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> SandboxPruneResult:
        """Safely prune detected stale resources or simulate in dry-run mode.

        Args:
            dry_run: When True, preview actions without mutating filesystem or DB.
            force: When True, delete dirty orphaned directories containing uncommitted files.

        Returns:
            Structured SandboxPruneResult summarizing processed items.
        """
        if dry_run:
            return self._execute_prune(dry_run=True, force=force)

        try:
            with WorkspaceLock(RepositoryPaths.from_root(self.path).lock_file):
                return self._execute_prune(dry_run=False, force=force)
        except LockTimeoutError as exc:
            return SandboxPruneResult(
                status=SandboxPruneStatus.LOCKED,
                dry_run=dry_run,
                force=force,
                errors=[f"Failed to acquire workspace lock: {exc}"],
            )


def prune_stale_sandboxes(
    path: Path,
    db: SandboxesRepository,
    *,
    dry_run: bool = False,
    force: bool = False,
    runs_db: RunsRepository | None = None,
) -> SandboxPruneResult:
    """Non-raising helper to execute safe sandbox pruning.

    Args:
        path: Repository root directory.
        db: SandboxesRepository instance.
        dry_run: When True, simulate without mutations.
        force: When True, delete dirty orphaned directories.
        runs_db: Optional RunsRepository instance for run liveness checks.

    Returns:
        Structured SandboxPruneResult.
    """
    pruner = SandboxPruner(path, db, runs_db=runs_db)
    return pruner.prune(dry_run=dry_run, force=force)
