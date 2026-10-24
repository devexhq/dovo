"""Safe prune execution service for stale worktrees and resources."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.db import SessionsRepository, WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    PruneAction,
    PrunedItem,
    StaleWorktreeCategory,
    StaleWorktreeItem,
    WorktreeDetectionStatus,
    WorktreePruneResult,
    WorktreePruneStatus,
)
from dovo.core.worktree.services.detector import WorktreeDetector
from dovo.core.worktree.services.failure_log import DiagnosticContext, logged_failure, record_fallback


def _item_context(item: StaleWorktreeItem) -> DiagnosticContext:
    """Return the diagnostic context of a prune action: worktree ID, branch and path."""
    return {
        "worktree_id": item.session_id,
        "branch": item.branch_name,
        "path": str(item.path) if item.path else None,
    }


class WorktreePruner:
    """Safe cleanup executor for stale worktrees, orphaned directories, and temporary branches."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: WorktreesRepository,
        *,
        git: GitRunner,
        sessions_db: SessionsRepository | None = None,
    ) -> None:
        """Initialize pruner bound to the workspace paths and database.

        Args:
            paths: Resolved command-invocation workspace paths; orphans are found under paths.worktrees_dir.
            db: WorktreesRepository instance.
            git: Runner for every git command.
            sessions_db: Optional SessionsRepository instance for run liveness checks.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.sessions_db = sessions_db
        self.git = git
        self.detector = WorktreeDetector(paths, db, git=git, sessions_db=sessions_db)

    def _prune_stale_worktree_ref(self, item: StaleWorktreeItem, *, dry_run: bool) -> PrunedItem:
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
            self.git.worktree_prune(self.path)
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
                error=logged_failure(
                    f"Failed to prune git worktree ref: {exc}",
                    exc,
                    operation="worktree.prune",
                    context=_item_context(item),
                ),
            )

    def _remove_orphaned_dir(self, dir_path: Path, *, context: DiagnosticContext) -> str | None:
        """Remove dir_path with git worktree remove, falling back to rmtree; record the fallback or its failure."""
        if not dir_path.exists():
            return None
        try:
            self.git.worktree_remove(self.path, dir_path, force=True)
            return None
        except Exception as exc:
            try:
                shutil.rmtree(dir_path)
            except Exception as rm_exc:
                return logged_failure(
                    f"Failed to remove directory '{dir_path}': {rm_exc}",
                    rm_exc,
                    operation="worktree.prune",
                    context=context,
                )
            record_fallback(exc, operation="worktree.prune", context=context)
            return None

    def _cleanup_orphaned_dir_and_db(self, item: StaleWorktreeItem) -> list[str]:
        """Perform disk deletion and database status update for an orphaned directory."""
        errors: list[str] = []
        context = _item_context(item)
        if item.path is not None:
            dir_err = self._remove_orphaned_dir(item.path, context=context)
            if dir_err:
                errors.append(dir_err)

        if item.session_id is not None:
            try:
                self.db.update_status(item.session_id, WorktreeStatus.CLEANED)
            except Exception as db_exc:
                errors.append(
                    logged_failure(
                        f"Failed to update database status for '{item.session_id}': {db_exc}",
                        db_exc,
                        operation="worktree.prune",
                        context=context,
                    )
                )

        try:
            self.git.worktree_prune(self.path)
        except Exception as exc:
            record_fallback(exc, operation="worktree.prune", context=context)

        return errors

    def _prune_orphaned_directory(
        self,
        item: StaleWorktreeItem,
        *,
        dry_run: bool,
        force: bool,
    ) -> PrunedItem:
        """Remove unindexed or non-active worktree directory safely."""
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

    def _prune_stale_db_record(self, item: StaleWorktreeItem, *, dry_run: bool) -> PrunedItem:
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
            self.db.update_status(item.identifier, WorktreeStatus.CLEANED)
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
                error=logged_failure(
                    f"Failed to update database status for '{item.identifier}': {exc}",
                    exc,
                    operation="worktree.prune",
                    context=_item_context(item),
                ),
            )

    def _prune_stale_branch(self, item: StaleWorktreeItem, *, dry_run: bool) -> PrunedItem:
        """Delete unlinked temporary worktree branches."""
        if dry_run:
            return PrunedItem(
                category=item.category,
                identifier=item.identifier,
                action=PruneAction.PRUNED,
                branch_name=item.branch_name,
                reason=f"Would prune: {item.reason}" if item.reason else "Would prune",
            )

        try:
            self.git.branch_delete(self.path, item.identifier, force=True)
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
                error=logged_failure(
                    f"Failed to delete branch '{item.identifier}': {exc}",
                    exc,
                    operation="worktree.prune",
                    context=_item_context(item),
                ),
            )

    def _process_item(
        self,
        item: StaleWorktreeItem,
        *,
        dry_run: bool,
        force: bool,
    ) -> PrunedItem:
        """Route stale item to the appropriate category handler."""
        if item.category == StaleWorktreeCategory.STALE_WORKTREE_REF:
            return self._prune_stale_worktree_ref(item, dry_run=dry_run)
        elif item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY:
            return self._prune_orphaned_directory(item, dry_run=dry_run, force=force)
        elif item.category == StaleWorktreeCategory.STALE_DB_RECORD:
            return self._prune_stale_db_record(item, dry_run=dry_run)
        elif item.category == StaleWorktreeCategory.STALE_BRANCH:
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
    ) -> WorktreePruneResult:
        """Internal execution routine following stale resource detection."""
        detection = self.detector.detect()
        if not detection.ok:
            status = (
                WorktreePruneStatus.GIT_FAILED
                if detection.status == WorktreeDetectionStatus.GIT_FAILED
                else WorktreePruneStatus.ERROR
            )
            return WorktreePruneResult(
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

        status = WorktreePruneStatus.PARTIAL_SUCCESS if errors else WorktreePruneStatus.OK

        return WorktreePruneResult(
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
    ) -> WorktreePruneResult:
        """Safely prune detected stale resources or simulate in dry-run mode.

        Args:
            dry_run: When True, preview actions without mutating filesystem or DB.
            force: When True, delete dirty orphaned directories containing uncommitted files.

        Returns:
            Structured WorktreePruneResult summarizing processed items.
        """
        if dry_run:
            return self._execute_prune(dry_run=True, force=force)

        try:
            with WorkspaceLock(self.paths.lock_file):
                return self._execute_prune(dry_run=False, force=force)
        except LockTimeoutError as exc:
            return WorktreePruneResult(
                status=WorktreePruneStatus.LOCKED,
                dry_run=dry_run,
                force=force,
                errors=[f"Failed to acquire workspace lock: {exc}"],
            )
